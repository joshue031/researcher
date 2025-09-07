document.addEventListener('DOMContentLoaded', () => {

    // Initialize markdown-it with plugins
    const md = window.markdownit({
        html: false,
        linkify: true,
        typographer: true,
        highlight: function (str, lang) {
            if (lang && window.hljs && window.hljs.getLanguage(lang)) {
                try {
                    return '<pre class="hljs"><code>' +
                           window.hljs.highlight(str, { language: lang, ignoreIllegals: true }).value +
                           '</code></pre>';
                } catch (__) {}
            }
            return '<pre class="hljs"><code>' + md.utils.escapeHtml(str) + '</code></pre>';
        }
    }).use(window.mdKatex);

    // --- STATE ---
    let currentProjectId = null;
    let currentConversationId = null;
    let projectData = {}; // Cache for current project's documents and BibTeX entries
    let taskPollingInterval = null;

    // --- DOM SELECTORS ---
    const sidebarToggleBtn = document.getElementById('sidebar-toggle-btn');
    const projectSelector = document.getElementById('project-selector');
    const newProjectBtn = document.getElementById('new-project-btn');
    const sidebarNavSection = document.getElementById('sidebar-nav-section');
    const contentDisplayArea = document.getElementById('content-display-area');
    const sidebarTabLinks = document.querySelectorAll('.sidebar-tab-link');
    const sidebarListContainer = document.getElementById('sidebar-list-container');
    //const downloadBibtexLink = document.getElementById('download-bibtex-link');
    const newTaskModal = document.getElementById('new-task-modal');
    const newTaskForm = document.getElementById('new-task-form');

    // Modals
    const newProjectModal = document.getElementById('new-project-modal');
    const newProjectForm = document.getElementById('new-project-form');
    const addReferenceModal = document.getElementById('add-reference-modal');
    const addReferenceForm = document.getElementById('add-reference-form');
    const closeModalBtns = document.querySelectorAll('[data-close-modal]');

    // --- API & DATA HANDLING ---
    const API_BASE_URL = '/api';

    const MOCK_CONVERSATIONS = [
        { id: 'conv1', title: 'Initial Query', messages: [] }
    ];

    async function fetchProjects() {
        try {
            const response = await fetch(`${API_BASE_URL}/projects`);
            if (!response.ok) throw new Error('Failed to fetch projects');
            const projects = await response.json();
            populateProjectSelector(projects);
        } catch (error) { console.error('Error fetching projects:', error); }
    }

    async function fetchProjectDetails(projectId) {
        try {
            const response = await fetch(`${API_BASE_URL}/projects/${projectId}`);
            if (!response.ok) throw new Error('Failed to fetch project details');
            return await response.json();
        } catch (error) { console.error('Error fetching project details:', error); return null; }
    }

    async function createProject(name) {
        try {
            const response = await fetch(`${API_BASE_URL}/projects`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name })
            });
            if (!response.ok) throw new Error((await response.json()).error);
            const newProject = await response.json();
            await fetchProjects();
            projectSelector.value = newProject.id;
            projectSelector.dispatchEvent(new Event('change'));
            closeModal(newProjectModal);
        } catch (error) { alert(`Error: ${error.message}`); }
    }

    async function uploadReference(projectId, formData) {
        const processingText = addReferenceModal.querySelector('.processing-text');
        try {
            processingText.classList.remove('hidden');
            const response = await fetch(`${API_BASE_URL}/projects/${projectId}/documents`, {
                method: 'POST',
                body: formData
            });
            if (!response.ok) {
                 const errData = await response.json();
                 throw new Error(errData.error || 'File upload failed');
            }
            // After uploading, refresh the project data to show the new reference
            closeModal(addReferenceModal);
            await handleProjectSelection({ target: { value: projectId } }); // Refresh data
        } catch(error) {
            console.error('Error uploading reference:', error);
            alert(`Upload Error: ${error.message}`);
        } finally {
            processingText.classList.add('hidden');
        }
    }
    
    async function askQuestion(projectId, question) {
        const chatDisplay = document.getElementById('chat-display');
        const chatInput = document.getElementById('chat-input');
        
        addChatMessageToDisplay(chatDisplay, 'user', question);
        chatInput.value = '';
        chatInput.disabled = true;

        try {
            const response = await fetch(`${API_BASE_URL}/projects/${projectId}/ask`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ question, conversation_id: currentConversationId })
            });
            if (!response.ok) throw new Error('Failed to get answer from API.');
            const data = await response.json();
            addChatMessageToDisplay(chatDisplay, 'assistant', data.answer);

            // Add new messages to local state to keep UI synced
            const convo = projectData.conversations.find(c => c.id === currentConversationId);
            if (convo) {
                convo.messages.push(data.user_message, data.assistant_message);
            }

        } catch (error) {
            addChatMessageToDisplay(chatDisplay, 'assistant', `Sorry, an error occurred: ${error.message}`, true);
        } finally {
            chatInput.disabled = false;
            chatInput.focus();
        }
    }

    async function createNewConversation(projectId) {
        const title = prompt("Enter a title for the new conversation:", "New Conversation");
        if (!title) return;
        try {
            const response = await fetch(`${API_BASE_URL}/projects/${projectId}/conversations`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ title })
            });
            if (!response.ok) throw new Error((await response.json()).error);
            const newConvo = await response.json();
            projectData.conversations.push(newConvo); // Update local state
            renderConversationsList(); // Re-render the list
            // Find the new item and click it to open
            sidebarListContainer.querySelector(`.sidebar-list-item[data-id='${newConvo.id}']`).click();
        } catch(error) { alert(`Error creating conversation: ${error.message}`); }
    }

    async function deleteConversation(conversationId) {
        if (!confirm("Are you sure you want to delete this conversation?")) return;
        try {
            const response = await fetch(`${API_BASE_URL}/conversations/${conversationId}`, { method: 'DELETE' });
            if (!response.ok) throw new Error((await response.json()).error);
            // Remove from local state
            projectData.conversations = projectData.conversations.filter(c => c.id !== conversationId);
            renderConversationsList();
            showWelcomeView(); // Go back to welcome screen
        } catch (error) { alert(`Error deleting conversation: ${error.message}`); }
    }

    async function deleteReference(documentId) {
        if (!confirm("Are you sure you want to delete this reference? This will also rebuild the project's search index.")) return;
        try {
            const response = await fetch(`${API_BASE_URL}/documents/${documentId}`, { method: 'DELETE' });
            if (!response.ok) throw new Error((await response.json()).error);
            // Remove from local state
            projectData.documents = projectData.documents.filter(d => d.id !== documentId);
            renderReferencesList();
            showWelcomeView();
        } catch (error) { alert(`Error deleting reference: ${error.message}`); }
    }

    async function createTask(projectId, taskType, userPrompt) {
        try {
            const response = await fetch(`${API_BASE_URL}/projects/${projectId}/tasks`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ task_type: taskType, user_prompt: userPrompt })
            });
            if (!response.ok) throw new Error((await response.json()).error);
            const newTask = await response.json();
            
            // Now, trigger the task to run
            await fetch(`${API_BASE_URL}/tasks/${newTask.id}/run`, { method: 'POST' });
            
            // Refresh the project data to include the new task
            await handleProjectSelection({ target: { value: projectId } });
            
            // Switch to the tasks tab and open the new task
            document.querySelector('.sidebar-tab-link[data-tab="tasks"]').click();
            sidebarListContainer.querySelector(`.sidebar-list-item[data-id='task-${newTask.id}']`).click();

            closeModal(newTaskModal);
        } catch(error) {
            alert(`Error creating task: ${error.message}`);
        }
    }
    
    async function fetchTaskStatus(taskId) {
        try {
            const response = await fetch(`${API_BASE_URL}/tasks/${taskId}`);
            if (!response.ok) {
                // Stop polling if the task is not found
                stopTaskPolling();
                return null;
            }
            return await response.json();
        } catch (error) {
            console.error('Failed to fetch task status:', error);
            stopTaskPolling();
            return null;
        }
    }


    // --- VIEW RENDERING & UI UPDATES ---

    function populateProjectSelector(projects) {
        const currentVal = projectSelector.value;
        projectSelector.innerHTML = '<option value="">Select a Project...</option>';
        projects.forEach(p => {
            const option = document.createElement('option');
            option.value = p.id;
            option.textContent = p.name;
            projectSelector.appendChild(option);
        });
        projectSelector.value = currentVal;
    }

    function showWelcomeView() {
        contentDisplayArea.innerHTML = `
            <div class="welcome-view">
                <h1>Welcome to your Local RAG Assistant</h1>
                <p>Select a project to begin, or create a new one.</p>
            </div>
        `;
    }

    function showChatView(conversation) {
        currentConversationId = conversation.id;
        contentDisplayArea.innerHTML = `
            <div class="chat-view">
                <div id="chat-display"></div>
                <div id="chat-input-area">
                    <form id="chat-form"><textarea id="chat-input" placeholder="Ask a question..." rows="1"></textarea><button type="submit">Send</button></form>
                </div>
            </div>`;
        const chatDisplay = document.getElementById('chat-display');
        if (conversation.messages.length === 0) {
            addChatMessageToDisplay(chatDisplay, 'assistant', "This is a new conversation. Ask a question about the project's documents to begin.");
        } else {
            conversation.messages.forEach(msg => addChatMessageToDisplay(chatDisplay, msg.role, msg.content));
        }
        document.getElementById('chat-form').addEventListener('submit', (e) => {
             e.preventDefault();
             const question = document.getElementById('chat-input').value.trim();
             if (question && currentProjectId && currentConversationId) {
                askQuestion(currentProjectId, question);
             }
        });
    }
    
    async function showReferenceView(reference) {

        const bibData = reference.bibtex_data || {};
        
        contentDisplayArea.innerHTML = `
            <div class="reference-view">
                <h2>${reference.title}</h1>
                <br>
                <p>${reference.description}</p>
                <br>
                <h3>Bibliographic Information</h3>
                <div class="bib-details">
                    <div class="bib-detail-item"><strong>Author(s):</strong> <span>${bibData.author || 'N/A'}</span></div>
                    <div class="bib-detail-item"><strong>Year:</strong> <span>${bibData.year || 'N/A'}</span></div>
                    <div class="bib-detail-item"><strong>Citation Key:</strong> <code>${bibData.key || 'N/A'}</code></div>
                </div>
                <h3>Full BibTeX Entry</h3>
                <br>
                <pre><code>${bibData.full_entry || 'No BibTeX entry available.'}</code></pre>
                <br>
                <div id="figures-container"></div>
            </div>
        `;

        try {
            const figuresResponse = await fetch(`${API_BASE_URL}/documents/${reference.id}/figures`);
            
            // Only proceed if the fetch was successful (e.g., not a 404 or 500 error)
            if (figuresResponse.ok) {
                const figures = await figuresResponse.json();
                
                // CRITICAL CHECK: Only try to display figures if the array exists and is not empty.
                if (figures && figures.length > 0) {
                    const figuresContainer = document.getElementById('figures-container');
                    let figuresHtml = '<h3>Extracted Figures</h3><div class="figures-gallery">';
                    
                    for (const fig of figures) {
                        // The path from the DB is relative, so we build the full API URL
                        const imageUrl = `${API_BASE_URL}/projects/${currentProjectId}/figures/${fig.image_path}`;
                        figuresHtml += `
                            <div class="figure-card">
                                <img src="${imageUrl}" alt="Page ${fig.page_number}: ${fig.description}" loading="lazy">
                                <div class="figure-info">
                                    <strong>${fig.name} (Page ${fig.page_number})</strong>
                                    <p>${fig.description}</p>
                                    <p>${fig.analysis}</p>
                                    <p>${fig.extracted_text}</p>
                                </div>
                            </div>
                        `;
                    }
                    figuresHtml += '</div>';
                    figuresContainer.innerHTML = figuresHtml; // Safely inject the HTML
                }
                // If figures.length is 0, we simply do nothing, and no error occurs.
            }
        } catch (error) {
            console.error("Failed to fetch or render figures:", error);
            // We can optionally display an error message in the UI here
        }
    }

    function addChatMessageToDisplay(display, sender, message, isError = false) {
        const bubble = document.createElement('div');
        bubble.className = `chat-bubble ${sender}`;
        if (isError) {
            bubble.classList.add('error');
            bubble.textContent = message; // Keep errors as plain text
        } else {
            // 1. Render markdown to HTML
            const rawHtml = md.render(message);
            // 2. Sanitize the HTML to prevent XSS attacks
            const sanitizedHtml = DOMPurify.sanitize(rawHtml);
            // 3. Set the innerHTML
            bubble.innerHTML = sanitizedHtml;
        }
        
        display.appendChild(bubble);
        addCopyButtonsToCodeBlocks(bubble); // Add copy buttons after rendering
        display.scrollTop = display.scrollHeight; // Scroll to bottom
    }

    function addCopyButtonsToCodeBlocks(container) {
        const codeBlocks = container.querySelectorAll('pre');
        codeBlocks.forEach(block => {
            const copyButton = document.createElement('button');
            copyButton.className = 'copy-code-btn';
            copyButton.textContent = 'Copy';
            
            copyButton.addEventListener('click', () => {
                const code = block.querySelector('code').innerText;
                navigator.clipboard.writeText(code).then(() => {
                    copyButton.textContent = 'Copied!';
                    setTimeout(() => {
                        copyButton.textContent = 'Copy';
                    }, 2000);
                });
            });
            
            block.appendChild(copyButton);
        });
    }

    function renderConversationsList() {
        sidebarListContainer.innerHTML = `<div class="sidebar-list-item-placeholder"><button id="new-conversation-btn" class="btn-secondary">+ New Conversation</button></div>`;
        document.getElementById('new-conversation-btn').addEventListener('click', () => createNewConversation(currentProjectId));
        
        projectData.conversations.forEach(convo => {
            const item = document.createElement('div');
            item.className = 'sidebar-list-item';
            item.dataset.id = convo.id;
            item.innerHTML = `
                <div class="sidebar-list-item-content">
                    <h4>${convo.title}</h4>
                </div>
                <button class="delete-btn" data-id="${convo.id}">&times;</button>
            `;
            item.querySelector('.sidebar-list-item-content').addEventListener('click', () => {
                document.querySelectorAll('.sidebar-list-item.active').forEach(i => i.classList.remove('active'));
                item.classList.add('active');
                showChatView(convo);
            });
            item.querySelector('.delete-btn').addEventListener('click', (e) => {
                e.stopPropagation(); // Prevent the item click from firing
                deleteConversation(convo.id);
            });
            sidebarListContainer.appendChild(item);
        });
    }

    async function deleteTask(taskId) {
        if (!confirm("Are you sure you want to delete this task?")) return;
        try {
            const response = await fetch(`${API_BASE_URL}/tasks/${taskId}`, { method: 'DELETE' });
            if (!response.ok) throw new Error((await response.json()).error);
            
            // Remove from local state and re-render the list
            projectData.tasks = projectData.tasks.filter(t => t.id !== taskId);
            renderTasksList();
            showWelcomeView(); // Go back to the welcome screen
        } catch (error) {
            alert(`Error deleting task: ${error.message}`);
        }
    }

    function renderTasksList() {
        sidebarListContainer.innerHTML = `<div class="sidebar-list-item-placeholder"><button id="new-task-btn" class="btn-secondary">+ New Task</button></div>`;
        document.getElementById('new-task-btn').addEventListener('click', () => showModal(newTaskModal));

        if (projectData.tasks) {
            projectData.tasks.forEach(task => {
                const item = document.createElement('div');
                item.className = 'sidebar-list-item';
                item.dataset.id = `task-${task.id}`;
                // Add the delete button to the HTML structure
                item.innerHTML = `
                    <div class="sidebar-list-item-content">
                        <h4>Report: ${task.user_prompt.substring(0, 25)}...</h4>
                        <p>Status: ${task.status}</p>
                    </div>
                    <button class="delete-btn" data-id="${task.id}">&times;</button>
                `;
                
                // Add click listener for VIEWING the task
                item.querySelector('.sidebar-list-item-content').addEventListener('click', () => {
                    document.querySelectorAll('.sidebar-list-item.active').forEach(i => i.classList.remove('active'));
                    item.classList.add('active');
                    showTaskDashboardView(task);
                });

                // Add click listener for DELETING the task
                item.querySelector('.delete-btn').addEventListener('click', (e) => {
                    e.stopPropagation(); // Prevent the main item click
                    deleteTask(task.id);
                });

                sidebarListContainer.appendChild(item);
            });
        }
    }

    // Polling logic for live updates
    function startTaskPolling(taskId) {
        stopTaskPolling(); // Ensure no other pollers are running
        taskPollingInterval = setInterval(async () => {
            const updatedTask = await fetchTaskStatus(taskId);
            if (updatedTask) {
                // Update the sidebar list item's text and status
                const sidebarItem = sidebarListContainer.querySelector(`.sidebar-list-item[data-id='task-${taskId}']`);
                if (sidebarItem) {
                    // Find the p tag inside, which holds the status
                    const statusElement = sidebarItem.querySelector('p');
                    if (statusElement) {
                        statusElement.textContent = `Status: ${updatedTask.status}`;
                    }
                }

                // If the currently viewed task is the one we are polling for, update the main dashboard view
                const activeTaskItem = document.querySelector('.sidebar-list-item.active');
                if (activeTaskItem && activeTaskItem.dataset.id === `task-${taskId}`) {
                    showTaskDashboardView(updatedTask); // Re-render the dashboard
                }

                if (updatedTask.status === 'complete' || updatedTask.status === 'failed') {
                    stopTaskPolling();
                }
            }
        }, 3000); // Poll every 3 seconds
    }

    function stopTaskPolling() {
        if (taskPollingInterval) {
            clearInterval(taskPollingInterval);
            taskPollingInterval = null;
        }
    }

    function renderReferencesList() {
        sidebarListContainer.innerHTML = `
            <div class="sidebar-list-item-placeholder">
                <a href="#" id="download-bibtex-link" class="btn-secondary" download>Download .bib</a>
            </div>
            <div class="sidebar-list-item-placeholder">
                <button id="add-reference-btn" class="btn-secondary">+ Add Reference</button>
            </div>
            `;
        document.getElementById('download-bibtex-link').href = `${API_BASE_URL}/projects/${currentProjectId}/bibtex`;
        document.getElementById('add-reference-btn').addEventListener('click', () => showModal(addReferenceModal));
        
        projectData.documents.forEach(doc => {
            const item = document.createElement('div');
            item.className = 'sidebar-list-item';
            item.dataset.id = doc.id;
            item.innerHTML = `
                <div class="sidebar-list-item-content">
                    <h4>${doc.title}</h4>
                    <p>${doc.filename}</p>
                </div>
                <button class="delete-btn" data-id="${doc.id}">&times;</button>
            `;
            item.querySelector('.sidebar-list-item-content').addEventListener('click', () => {
                document.querySelectorAll('.sidebar-list-item.active').forEach(i => i.classList.remove('active'));
                item.classList.add('active');
                showReferenceView(doc);
            });
            item.querySelector('.delete-btn').addEventListener('click', (e) => {
                e.stopPropagation();
                deleteReference(doc.id);
            });
            sidebarListContainer.appendChild(item);
        });
    }

    async function handleProjectSelection(e) {
        const projectId = e.target.value;
        if (projectId) {
            currentProjectId = projectId;
            const details = await fetchProjectDetails(projectId);
            if(details) {
                projectData = details;

                // Add tasks to projectData for the renderer
                const tasksResponse = await fetch(`${API_BASE_URL}/projects/${projectId}/tasks`);
                projectData.tasks = await tasksResponse.json();
                
                // Show the navigation section
                sidebarNavSection.classList.remove('hidden');
                
                // Manually set the conversations tab to active and render its content
                document.querySelector('.sidebar-tab-link[data-tab="conversations"]').click();
                
                // Automatically select and show the first conversation
                const firstConvoItem = sidebarListContainer.querySelector('.sidebar-list-item');
                if (firstConvoItem) {
                    firstConvoItem.click();
                } else {
                    showWelcomeView();
                }
            }
        } else {
            currentProjectId = null;
            projectData = {};
            sidebarNavSection.classList.add('hidden');
            showWelcomeView();
        }
    }

    function showTaskDashboardView(task) {
        stopTaskPolling(); // Stop any previous polling

        // Function to generate the progress list HTML
        function getProgressHtml(status) {
            const steps = [
                { id: 'gathering_context', text: 'Analyzing references...' },
                { id: 'generating_outline', text: 'Generating outline...' },
                { id: 'writing_section', text: 'Writing report sections...' },
                { id: 'assembling_report', text: 'Assembling final report...' },
                { id: 'complete', text: 'Task complete!' }
            ];

            let html = '<ul class="task-progress-tracker">';
            let currentStepReached = true;
            
            for(const step of steps) {
                let state = 'pending';
                if (status === 'failed') {
                    state = 'failed';
                } else if (status.startsWith(step.id) || status === step.id) {
                    state = 'in-progress';
                    currentStepReached = false;
                } else if (currentStepReached || status === 'complete') {
                    state = 'complete';
                }
                
                // Special case for 'complete' status
                if (status === 'complete' && step.id === 'complete') {
                    state = 'complete';
                }

                html += `<li class="${state}"><span class="icon"></span> ${step.text}</li>`;
            }
            html += '</ul>';
            return html;
        }
        
        // Function to generate the outputs section HTML
        function getOutputsHtml(task) {
            if (task.status === 'failed' || task.status === 'complete') {
                let html = '<div class="task-section-content task-outputs">';
                if (task.has_outline) {
                    html += `<div class="output-item"><span>Report Outline</span><a href="${API_BASE_URL}/tasks/${task.id}/outline" class="btn-secondary" download>Download .json</a></div>`;
                }
                if (task.final_markdown_content) {
                    html += `<div class="output-item"><span>Report Draft (.md)</span><a href="${API_BASE_URL}/tasks/${task.id}/markdown" class="btn-secondary" download>Download</a></div>`;
                }
                if (task.has_final_report) {
                    html += `<div class="output-item"><span>Final Report</span><a href="${API_BASE_URL}/tasks/${task.id}/report" class="btn-secondary" download>Download .tex</a></div>`;
                }
                html += '</div>';
                return html;
            }
            return '';
        }

        // Function to generate the preview
        function getPreviewHtml(task) {
            if (task.final_markdown_content) {
                const rawHtml = md.render(task.final_markdown_content);
                const sanitizedHtml = DOMPurify.sanitize(rawHtml);
                return `<div class="task-section-content report-preview-content">${sanitizedHtml}</div>`;
            }
            return '';
        }
        
        // Main template for the dashboard
        contentDisplayArea.innerHTML = `
            <div class="task-dashboard-view">
                <h1>Report Writing Task</h1>
                <span class="task-status-badge ${task.status.startsWith('writing') ? 'running' : task.status}">${task.status.replace(/_/g, ' ')}</span>
                
                <div class="task-section">
                    <div class="task-section-header">User Prompt</div>
                    <div class="task-section-content"><p>${task.user_prompt}</p></div>
                </div>

                <div class="task-section">
                    <div class="task-section-header">Progress</div>
                    <div class="task-section-content">${getProgressHtml(task.status)}</div>
                </div>
                
                <div id="task-outputs-section" class="task-section ${task.status === 'complete' || task.status === 'failed' ? '' : 'hidden'}">
                    <div class="task-section-header">Outputs</div>
                    ${getOutputsHtml(task)}
                </div>

                <div id="task-preview-section" class="task-section ${task.final_markdown_content ? '' : 'hidden'}">
                    <div class="task-section-header">Report Preview</div>
                    ${getPreviewHtml(task)}
                </div>
            </div>
        `;

        // Add copy buttons to any code blocks in the preview
        const previewArea = document.getElementById('task-preview-section');
        if(previewArea) {
            addCopyButtonsToCodeBlocks(previewArea);
        }

        // Start polling for updates if the task is still running
        if (task.status !== 'complete' && task.status !== 'failed') {
            startTaskPolling(task.id);
        }
    }
    
    // --- EVENT LISTENERS & INITIALIZATION ---

    sidebarToggleBtn.addEventListener('click', () => {
        // We toggle the class on the BODY so we can style the button and sidebar easily
        document.body.classList.toggle('sidebar-collapsed');
    });
    projectSelector.addEventListener('change', handleProjectSelection);
    
    sidebarTabLinks.forEach(tab => {
        tab.addEventListener('click', () => {
            sidebarTabLinks.forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            
            // Call the correct render function based on the tab clicked
            const tabName = tab.dataset.tab;
            if (tabName === 'conversations') {
                renderConversationsList();
            } else if (tabName === 'tasks') {
                renderTasksList();
            } else if (tabName === 'references') {
                renderReferencesList();
            }
        });
    });

    // Modal handling
    function showModal(modal) { modal.classList.remove('hidden'); }
    function closeModal(modal) { modal.classList.add('hidden'); }
    newProjectBtn.addEventListener('click', () => showModal(newProjectModal));
    closeModalBtns.forEach(btn => btn.addEventListener('click', () => closeModal(btn.closest('.modal-overlay'))));
    
    newProjectForm.addEventListener('submit', (e) => {
        e.preventDefault();
        const name = document.getElementById('new-project-name').value.trim();
        if (name) {
            createProject(name);
            newProjectForm.reset();
        }
    });

    addReferenceForm.addEventListener('submit', (e) => {
        e.preventDefault();
        const fileInput = document.getElementById('file-upload');
        const docType = document.getElementById('doc-type-selector').value;
        if (fileInput.files.length > 0 && currentProjectId) {
            const formData = new FormData();
            formData.append('file', fileInput.files[0]);
            formData.append('type', docType);
            uploadReference(currentProjectId, formData);
            addReferenceForm.reset();
        }
    });

    // Add event listener for the new task form
    newTaskForm.addEventListener('submit', (e) => {
        e.preventDefault();
        const taskType = document.getElementById('task-type-selector').value;
        const userPrompt = document.getElementById('task-prompt').value.trim();
        if (userPrompt && currentProjectId) {
            createTask(currentProjectId, taskType, userPrompt);
        }
    });

    showWelcomeView();
    fetchProjects();
});