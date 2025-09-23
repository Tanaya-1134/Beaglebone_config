function qs(id){ return document.getElementById(id); }


document.addEventListener('DOMContentLoaded', () => {
    // Get all the tab buttons
    const tabButtons = document.querySelectorAll('.flex.space-x-4 button');

    // Get all the content containers
    const contentContainers = document.querySelectorAll('[id^="content-"]');

    tabButtons.forEach(button => {
        button.addEventListener('click', () => {
            // Get the ID of the content container to show
            const targetId = `content-${button.getAttribute('data-tab')}`;

            // First, hide all content containers
            contentContainers.forEach(container => {
                container.classList.add('hidden');
            });

            // Then, show the correct one
            const targetContent = document.getElementById(targetId);
            if (targetContent) {
                targetContent.classList.remove('hidden');
            }

            // Update button styles to show which tab is active
            tabButtons.forEach(btn => {
                btn.classList.remove('border-blue-500');
                btn.classList.add('border-transparent');
                btn.classList.add('hover:border-gray');
            });
            button.classList.remove('border-transparent');
            button.classList.remove('hover:border-gray');
            button.classList.add('border-blue-500');
        });
    });
});

/* ---------- CPU vertical bar ---------- */
function buildCpuSegments(n){
  const bar = qs('cpuBar');
  if(!bar || bar.dataset.init) return;
  bar.dataset.init = '1';
  for(let i=0;i<n;i++){
    const seg = document.createElement('div');
    seg.className = 'cpu-seg';
    bar.appendChild(seg);
  }
}
function colorForPct(p){ if(p<=35) return 'green'; if(p<=50) return 'yellow'; if(p<=75) return 'orange'; return 'red'; }
function updateCpuBar(pct){
  const bar = qs('cpuBar'), pctLabel = qs('cpuPct'); if(!bar) return;
  const segs = Array.from(bar.children);
  const active = Math.round((pct/100)*segs.length);
  const color = colorForPct(pct);
  segs.forEach((el, idx)=>{ el.className = 'cpu-seg' + (idx < active ? (' on ' + color) : ''); });
  if(pctLabel) pctLabel.textContent = Math.round(pct) + '%';
}

/* ---------- Formatting ---------- */
function fmtFreq(khz){
  if(typeof khz !== 'number' || khz<=0) return '— MHz';
  const mhz = khz/1000;
  return (mhz>=1000) ? (mhz/1000).toFixed(2)+' GHz' : Math.round(mhz)+' MHz';
}
function fmtBounds(min_khz, max_khz){
  function f(k){ const s = fmtFreq(k); return s.replace('— ',''); }
  return f(min_khz)+' / '+f(max_khz);
}
function fmtUptime(secs){
  if(typeof secs !== 'number' || !(secs>=0)) return '—';
  const d=Math.floor(secs/86400); secs-=d*86400; const h=Math.floor(secs/3600); secs-=h*3600; const m=Math.floor(secs/60); const s=Math.floor(secs-m*60);
  return (d>0? d+'d ':'')+String(h).padStart(2,'0')+':'+String(m).padStart(2,'0')+':'+String(s).padStart(2,'0');
}

/* ---------- Meta cache ---------- */
const meta = { load:null, governor:null, min:null, max:null, up:null, freq:null, temp:null };
function setFreqTemp(freq_khz, temp_c){
  const f = qs('cpuFreq'); if(f) f.textContent = fmtFreq(freq_khz);
  const rowTemp = qs('rowTemp'), tv = qs('cpuTemp');
  const has = (typeof temp_c === 'number');
  if(rowTemp) rowTemp.style.display = has ? '' : 'none';
  if(has && tv) tv.textContent = temp_c.toFixed(1)+' °C';
}
function setMetaFull(j){
  meta.load = j.load || meta.load;
  meta.governor = (j.governor!=null)? j.governor : meta.governor;
  meta.min = (j.freq_min_khz!=null)? j.freq_min_khz : meta.min;
  meta.max = (j.freq_max_khz!=null)? j.freq_max_khz : meta.max;
  meta.up = (j.uptime_seconds!=null)? j.uptime_seconds : meta.up;
  meta.freq = (j.freq_khz!=null)? j.freq_khz : meta.freq;
  meta.temp = (j.temp_c!=null)? j.temp_c : meta.temp;

  setFreqTemp(meta.freq, meta.temp);

  const loadEl = qs('cpuLoad');
  if(loadEl && meta.load && typeof meta.load['1m']==='number'){
    loadEl.textContent = meta.load['1m'].toFixed(2)+' / '+meta.load['5m'].toFixed(2)+' / '+meta.load['15m'].toFixed(2);
  }
  const govEl = qs('cpuGov'); if(govEl) govEl.textContent = meta.governor || '—';
  const bEl = qs('cpuBounds'); if(bEl) bEl.textContent = fmtBounds(meta.min, meta.max);
  const upEl = qs('cpuUptime'); if(upEl) upEl.textContent = fmtUptime(meta.up);
}

/* ---------- Internet (General) ---------- */
async function pingInternet(){
  try{
    const r = await fetch('/api/ping-internet'); const j = await r.json();
    const dot = qs('genNetDot'), txt = qs('genNetTxt'); if(!dot||!txt) return;
    if(j.online){ dot.className='dot green-blink'; txt.textContent='Online'; }
    else{ dot.className='dot red'; txt.textContent='Offline'; }
  }catch(e){}
}

/* ---------- Lock / Unlock ---------- */

function setEditable(enabled){
  // remember global lock state for other helpers
  window.__unlocked = !!enabled;

  document.querySelectorAll('.need-unlock').forEach(el=>{
    el.disabled = !enabled;
  });

  const apply = qs('applyBtn');
  if(apply) apply.disabled = !enabled;

  // Re-evaluate dependent field states with the new lock state
  setNetworkFieldState();
  setTimeFieldState();
}

async function unlockFlow(){
  const modal = qs('unlockModal');
  const pwdInput = qs('pwdInput');
  const err = qs('unlockErr');
  err.textContent = ''; modal.classList.remove('hidden');
  pwdInput.value = ''; pwdInput.focus();

  return new Promise((resolve)=>{
    function done(ok){ modal.classList.add('hidden'); resolve(ok); }
    qs('cancelUnlock').onclick = ()=> done(false);
    qs('confirmUnlock').onclick = async ()=>{
      try{
        const r = await fetch('/auth/unlock',{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({password: pwdInput.value})});
        const j = await r.json();
        if(r.ok && j.ok){ done(true); }
        else{ err.textContent = j.error || 'Unlock failed'; }
      }catch(e){ err.textContent = 'Error: '+e; }
    };
  });
}
async function lockNow(){ await fetch('/auth/lock',{method:'POST'}); }

function initLockUI(){
  const btn = qs('unlockBtn');
  const status = qs('lockStatus');
  let unlocked = !!window.IS_UNLOCKED;
  function refresh(){
    setEditable(unlocked);
    if(btn) btn.textContent = unlocked ? 'Lock' : 'Unlock to Edit';
    if(status) status.textContent = unlocked ? 'Unlocked' : 'Locked';
  }
  refresh();
  if(btn){
    btn.addEventListener('click', async ()=>{
      if(unlocked){ await lockNow(); unlocked = false; refresh(); }
      else{ const ok = await unlockFlow(); if(ok){ unlocked = true; refresh(); } }
    });
  }
}

/* ---------- Form helpers ---------- */
function setNetworkFieldState(){
  const modeEl = qs('network_mode'); if(!modeEl) return;
  const unlocked = !!window.__unlocked;
  const isStatic = modeEl.value === 'static';
  ['ip','subnet','gateway','dns'].forEach(id=>{
    const el = qs(id); if(!el) return;
    el.disabled = !unlocked || !isStatic;
    el.classList.toggle('opacity-60', el.disabled);
  });
}

function setTimeFieldState(){
  const srcEl = qs('time_source'); if(!srcEl) return;
  const unlocked = !!window.__unlocked;
  const isManual = srcEl.value === 'manual';
  ['date','time'].forEach(id=>{
    const el = qs(id); if(!el) return;
    el.disabled = !unlocked || !isManual;
    el.classList.toggle('opacity-60', el.disabled);
  });
}

async function applyConfig(){
  const msg = qs('msg'); if(msg){ msg.textContent = ''; msg.className = 'mt-3 text-sm'; }
  const data = {
    hostname: qs('hostname').value,
    network_mode: qs('network_mode').value,
    ip: qs('ip').value, 
    subnet: qs('subnet').value, 
    gateway: qs('gateway').value, 
    dns: qs('dns').value,
    pressure_unit: qs('pressure_unit').value, 
    temperature_unit: qs('temperature_unit').value, 
    mode: qs('mode').value,
    time_source: qs('time_source').value, 
    date: qs('date').value, 
    time: qs('time').value,
    instrument_name: qs('instrument_name').value,
    instrument_ip: qs('instrument_ip').value,	  
  };
  try{
    const r = await fetch('/submit-data',{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(data)});
    const j = await r.json().catch(()=>({}));
    if(r.ok){
      if(msg){ msg.className='mt-3 text-sm msg-ok'; msg.textContent = j.message || 'Saved.'; }
    }else{
      const text = (j && (j.error || j.message)) || `HTTP ${r.status}`;
      if(msg){ msg.className='mt-3 text-sm msg-err'; msg.textContent = text; }
      if(r.status === 403 && msg){ msg.textContent += ' (Unlock to Edit first)'; }
      if((j && j.requires_root) && msg){ msg.textContent += ' (Run as root to apply system settings)'; }
    }
  }catch(e){
    if(msg){ msg.className='mt-3 text-sm msg-err'; msg.textContent = 'Unexpected error: '+e; }
  }
}

/* ---------- DB Log viewer ---------- */
async function viewDbLog(){
  const box = qs('dbLog'); if(!box) return;
  box.textContent = 'Loading log...';
  try{
    const r = await fetch('/api/db-log');
    const txt = await r.text();
    box.textContent = txt || 'No log yet.';
  }catch(e){
    box.textContent = 'Error loading log: ' + e;
  }
}

/* -------- General block: Date/Time -------- */
let __devTimeOffsetMs = 0; // device_time - browser_now

function updateGeneralClockTick(){
  const d = new Date(Date.now() + __devTimeOffsetMs);
  const dateEl = qs('genDate'), timeEl = qs('genTime');
  if(dateEl) dateEl.textContent = d.toLocaleDateString();
  if(timeEl) timeEl.textContent = d.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', second:'2-digit'});
}

function syncClockFromServer(iso){
  if(!iso) return;
  const serverMs = new Date(iso).getTime();
  if(!Number.isNaN(serverMs)){
    __devTimeOffsetMs = serverMs - Date.now();
    updateGeneralClockTick();
  }
}

/* ---------- Boot ---------- */
async function init(){
  buildCpuSegments(40);

  // Lock UI + initial enablement
  initLockUI();            // sets window.__unlocked and Apply btn
  setNetworkFieldState();  // ensure states reflect default lock
  setTimeFieldState();

  // Listeners
  const m = qs('network_mode'); if(m) m.addEventListener('change', setNetworkFieldState);
  const t = qs('time_source'); if(t) t.addEventListener('change', setTimeFieldState);
  const a = qs('applyBtn'); if(a) a.addEventListener('click', (e)=>{ e.preventDefault(); applyConfig(); });

  // NEW: moved button lives in its own panel
  const v = qs('viewLogBtn'); if(v) v.addEventListener('click', (e)=>{ e.preventDefault(); viewDbLog(); });

  // Connectivity
  pingInternet(); setInterval(pingInternet, 5000);

  // CPU animation
  let lastCpu = 0, cpuAnim = 0;
  function raf(){ cpuAnim += (lastCpu - cpuAnim)*0.2; updateCpuBar(Math.max(0, Math.min(100, cpuAnim))); requestAnimationFrame(raf); }
  requestAnimationFrame(raf);

  // Snapshot every 2s
  async function refresh(){ 
	try{ 
		const r = await fetch('/api/sysinfo'); 
		const j = await r.json();
		setMetaFull(j);
		if(j.server_time) syncClockFromServer(j.server_time);
	}catch(e){} 
  }
  setInterval(refresh, 2000); refresh();

  // Local 1-second tick for General Date/Time (uses server offset if available)
  updateGeneralClockTick();
  setInterval(updateGeneralClockTick, 1000);
  	
  // SSE fast path
  try{
    const es = new EventSource('/api/telemetry');
    es.onmessage = function(ev){
      const j = JSON.parse(ev.data);
      if(typeof j.cpu === 'number') lastCpu = j.cpu;
      if('freq_khz' in j || 'temp_c' in j) setFreqTemp(j.freq_khz, j.temp_c);
    };
  }catch(e){}
}
document.addEventListener('DOMContentLoaded', init);
