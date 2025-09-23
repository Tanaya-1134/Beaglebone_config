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
