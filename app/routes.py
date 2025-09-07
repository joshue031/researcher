import json
import os
import threading
from flask import Blueprint, request, jsonify, current_app, make_response, send_from_directory
from werkzeug.utils import secure_filename
from app import db
from app.models import Project, Document, Conversation, Message, Figure, Task
from app.llm_logic import process_and_embed_document, answer_question, rebuild_faiss_index_for_project
from app.agent_logic import run_report_writing_task

main = Blueprint('main', __name__)

@main.route('/')
def serve_index():
    """Serves the main index.html file."""
    return send_from_directory(current_app.static_folder, 'index.html')

@main.route('/api/projects', methods=['POST'])
def create_project():
    """Creates a new project."""
    data = request.get_json()
    if not data or not 'name' in data:
        return jsonify({'error': 'Project name is required'}), 400

    if Project.query.filter_by(name=data['name']).first():
        return jsonify({'error': 'A project with this name already exists'}), 409

    new_project = Project(name=data['name'])
    db.session.add(new_project)
    db.session.commit()

    # Create a dedicated directory for the project's data
    project_dir = os.path.join(current_app.config['PROJECTS_DATA_DIR'], str(new_project.id))
    os.makedirs(project_dir, exist_ok=True)

    return jsonify({'id': new_project.id, 'name': new_project.name}), 201

@main.route('/api/projects/<int:project_id>/documents', methods=['POST'])
def upload_document(project_id):
    """Uploads a document, processes it, and adds it to a project."""
    project = Project.query.get_or_404(project_id)
    
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    doc_type = request.form.get('type', 'misc') # e.g., 'journal_article', 'book'
    
    if file:
        filename = secure_filename(file.filename)
        project_dir = os.path.join(current_app.config['PROJECTS_DATA_DIR'], str(project_id))
        file_path = os.path.join(project_dir, filename)
        file.save(file_path)

        try:
            doc_record_dict = process_and_embed_document(project_id, file_path, doc_type, filename)
            return jsonify(doc_record_dict), 201 # Return the full dictionary
        except (ValueError, RuntimeError) as e:
            # Clean up failed upload
            os.remove(file_path)
            return jsonify({'error': str(e)}), 500

@main.route('/api/projects/<int:project_id>/ask', methods=['POST'])
def ask_project_question(project_id):
    """Asks a question, gets an answer, and saves the exchange to a conversation."""
    Project.query.get_or_404(project_id)
    data = request.get_json()
    if not data or 'question' not in data or 'conversation_id' not in data:
        return jsonify({'error': 'Question and conversation_id are required'}), 400

    question = data['question']
    conversation_id = data['conversation_id']
    
    conversation = Conversation.query.get_or_404(conversation_id)

    try:
        # Get the answer from the LLM
        answer_text = answer_question(project_id, question)

        # Save the user's message and the assistant's answer to the DB
        user_message = Message(conversation_id=conversation.id, role='user', content=question)
        assistant_message = Message(conversation_id=conversation.id, role='assistant', content=answer_text)

        db.session.add(user_message)
        db.session.add(assistant_message)
        db.session.commit()
        
        return jsonify({
            'answer': answer_text,
            'user_message': user_message.to_dict(),
            'assistant_message': assistant_message.to_dict()
        })

    except (FileNotFoundError):
        return jsonify({'error': 'Project data not found. Upload documents first.'}), 404
    except RuntimeError as e:
        return jsonify({'error': str(e)}), 500

@main.route('/api/projects/<int:project_id>/bibtex', methods=['GET'])
def download_bibtex(project_id):
    """Generates and serves a .bib file for the project."""
    project = Project.query.get_or_404(project_id)
    
    all_bibtex_entries = [doc.bibtex_full_entry for doc in project.documents if doc.bibtex_full_entry]
    if not all_bibtex_entries:
        return "No BibTeX entries found for this project.", 404
        
    bibtex_content = "\n\n".join(all_bibtex_entries)
    
    response = make_response(bibtex_content)
    response.headers['Content-Type'] = 'application/x-bibtex'
    response.headers['Content-Disposition'] = f'attachment; filename={secure_filename(project.name)}.bib'
    return response

@main.route('/api/projects', methods=['GET'])
def get_projects():
    """Gets a list of all projects."""
    projects = Project.query.order_by(Project.name).all()
    return jsonify([{'id': p.id, 'name': p.name} for p in projects])

@main.route('/api/projects/<int:project_id>', methods=['GET'])
def get_project_details(project_id):
    """Gets details, documents, and conversations for a single project."""
    project = Project.query.get_or_404(project_id)
    
    documents = [{
        'id': doc.id,
        'filename': doc.filename,
        'title': doc.title,
        'description': doc.description,
        'bibtex_data': {
            'key': doc.bibtex_key,
            'author': doc.bibtex_author,
            'year': doc.bibtex_year,
            'full_entry': doc.bibtex_full_entry
        },
        'uploaded_at': doc.uploaded_at.isoformat()
    } for doc in project.documents]

    conversations = [{
        'id': conv.id,
        'title': conv.title,
        'messages': [msg.to_dict() for msg in conv.messages]
    } for conv in project.conversations]

    return jsonify({
        'id': project.id,
        'name': project.name,
        'documents': documents,
        'conversations': conversations
    })

@main.route('/api/projects/<int:project_id>/conversations', methods=['POST'])
def create_conversation(project_id):
    """Creates a new, empty conversation in a project."""
    project = Project.query.get_or_404(project_id)
    data = request.get_json()
    title = data.get('title', 'New Conversation')

    new_convo = Conversation(project_id=project.id, title=title)
    db.session.add(new_convo)
    db.session.commit()

    return jsonify({
        'id': new_convo.id,
        'title': new_convo.title,
        'messages': []
    }), 201

@main.route('/api/conversations/<int:conversation_id>', methods=['DELETE'])
def delete_conversation(conversation_id):
    """Deletes a conversation and all its messages."""
    convo = Conversation.query.get_or_404(conversation_id)
    db.session.delete(convo)
    db.session.commit()
    return jsonify({'success': True, 'message': 'Conversation deleted.'}), 200

@main.route('/api/documents/<int:document_id>', methods=['DELETE'])
def delete_document(document_id):
    """
    Deletes a document, its physical file, all its associated figure images,
    and then rebuilds the project's vector index.
    """
    doc = Document.query.get_or_404(document_id)
    project_id = doc.project_id
    
    project_dir = os.path.join(current_app.config['PROJECTS_DATA_DIR'], str(project_id))
    
    # --- START OF NEW LOGIC ---
    # 1. Delete all associated figure image files
    print(f"Deleting associated figures for document {document_id}...")
    for fig in doc.figures:
        try:
            # fig.image_path is relative (e.g., 'figures/doc_1_p2_fig0.png')
            # So we join it with the project directory
            figure_file_path = os.path.join(project_dir, fig.image_path)
            if os.path.exists(figure_file_path):
                os.remove(figure_file_path)
                print(f"  > Deleted figure file: {figure_file_path}")
        except Exception as e:
            # Log the error but don't stop the process
            print(f"  ! Could not delete figure file {fig.image_path}: {e}")
    # --- END OF NEW LOGIC ---

    # 2. Delete the main physical document file (e.g., the PDF)
    try:
        main_file_path = os.path.join(project_dir, doc.filename)
        if os.path.exists(main_file_path):
            os.remove(main_file_path)
            print(f"Deleted main document file: {main_file_path}")
    except Exception as e:
        print(f"Could not delete main document file {doc.filename}: {e}")

    # 3. Delete the database record for the document.
    # The 'cascade' option in the model will automatically delete all associated 'Figure' records from the database.
    db.session.delete(doc)
    db.session.commit()
    print(f"Deleted document {document_id} and its figure records from the database.")

    # 4. (Optional but good practice) Clean up the 'figures' directory if it's now empty
    figures_dir = os.path.join(project_dir, 'figures')
    try:
        if os.path.exists(figures_dir) and not os.listdir(figures_dir):
            os.rmdir(figures_dir)
            print(f"Removed empty figures directory: {figures_dir}")
    except Exception as e:
        print(f"Could not remove empty figures directory: {e}")

    # 5. Rebuild the FAISS index for the entire project
    try:
        rebuild_faiss_index_for_project(project_id)
    except Exception as e:
        return jsonify({'error': f'DB records and files deleted, but failed to rebuild index: {e}'}), 500

    return jsonify({'success': True, 'message': 'Document and all associated files deleted; index rebuilt.'}), 200

@main.route('/api/documents/<int:document_id>/figures', methods=['GET'])
def get_document_figures(document_id):
    """Gets a list of all analyzed figures for a given document."""
    document = Document.query.get_or_404(document_id)
    figures = [fig.to_dict() for fig in document.figures]
    return jsonify(figures)

@main.route('/api/projects/<int:project_id>/figures/<path:filename>')
def serve_figure_image(project_id, filename):
    """Serves a specific figure image file from the project's data directory."""
    figures_dir = os.path.join(current_app.config['PROJECTS_DATA_DIR'], str(project_id))
    return send_from_directory(figures_dir, filename)

@main.route('/api/projects/<int:project_id>/tasks', methods=['GET', 'POST'])
def handle_tasks(project_id):
    project = Project.query.get_or_404(project_id)
    if request.method == 'GET':
        tasks = [task.to_dict() for task in project.tasks]
        return jsonify(tasks)
    
    if request.method == 'POST':
        data = request.get_json()
        if not data or 'user_prompt' not in data or 'task_type' not in data:
            return jsonify({'error': 'user_prompt and task_type are required'}), 400
        
        new_task = Task(
            project_id=project.id,
            user_prompt=data['user_prompt'],
            task_type=data['task_type']
        )
        db.session.add(new_task)
        db.session.commit()
        return jsonify(new_task.to_dict()), 201

@main.route('/api/tasks/<int:task_id>', methods=['GET', 'DELETE'])
def get_task_status(task_id):
    task = Task.query.get_or_404(task_id)
    
    if request.method == 'GET':
        return jsonify(task.to_dict())
    
    if request.method == 'DELETE':
        # Here you could also delete associated files if tasks generated them
        db.session.delete(task)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Task deleted.'}), 200

@main.route('/api/tasks/<int:task_id>/run', methods=['POST'])
def run_task(task_id):
    """Triggers a task to run in a background thread."""
    task = Task.query.get_or_404(task_id)
    if task.status.startswith('writing') or task.status.startswith('generating'):
        return jsonify({'error': 'Task is already running'}), 409

    # We need to pass the app instance to the thread
    app = current_app._get_current_object()

    if task.task_type == 'report_writing':
        thread = threading.Thread(target=run_report_writing_task, args=(task.id, app))
        thread.daemon = True
        thread.start()
        return jsonify({'message': 'Report generation task started.'}), 202
    else:
        return jsonify({'error': 'Unknown task type'}), 400

@main.route('/api/tasks/<int:task_id>/<artifact>', methods=['GET'])
def get_task_artifact(task_id, artifact):
    """Downloads a generated artifact like the outline or final report."""
    task = Task.query.get_or_404(task_id)
    content = ""
    filename = "download.txt"
    
    if artifact == 'outline' and task.outline_json:
        # We'll pretty-print the JSON for readability
        outline_obj = json.loads(task.outline_json)
        content = json.dumps(outline_obj, indent=2)
        filename = f"task_{task.id}_outline.json"
    elif artifact == 'report' and task.final_content:
        content = task.final_content
        filename = f"task_{task.id}_report.tex"
    elif artifact == 'markdown' and task.final_markdown_content:
        content = task.final_markdown_content
        filename = f"task_{task.id}_report.md"
    else:
        return jsonify({'error': 'Artifact not found or not yet generated'}), 404

    response = make_response(content)
    response.headers['Content-Type'] = 'text/plain'
    response.headers['Content-Disposition'] = f'attachment; filename={filename}'
    return response