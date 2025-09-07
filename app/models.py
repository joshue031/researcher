from app import db
from datetime import datetime

class Project(db.Model):
    """Represents a user-created project."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    documents = db.relationship('Document', backref='project', lazy=True, cascade="all, delete-orphan")
    conversations = db.relationship('Conversation', backref='project', lazy=True, cascade="all, delete-orphan")
    tasks = db.relationship('Task', backref='project', lazy=True, cascade="all, delete-orphan")

    def __repr__(self):
        return f'<Project {self.name}>'

class Document(db.Model):
    """Represents a document within a project."""
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)
    filename = db.Column(db.String(300), nullable=False)
    document_type = db.Column(db.String(50), nullable=False) # e.g., 'journal_article', 'book'
    title = db.Column(db.String(300), nullable=False)
    description = db.Column(db.Text, nullable=True)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    figures = db.relationship('Figure', backref='document', lazy=True, cascade="all, delete-orphan")

    # BibTeX information
    bibtex_key = db.Column(db.String(100), nullable=True, unique=True)
    bibtex_author = db.Column(db.Text, nullable=True)
    bibtex_year = db.Column(db.String(20), nullable=True)
    # Store the full, raw entry as well for easy reconstruction
    bibtex_full_entry = db.Column(db.Text, nullable=True)

    def __repr__(self):
        return f'<Document {self.filename}>'

class Figure(db.Model):
    """Represents an extracted and analyzed figure from a document."""
    id = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(db.Integer, db.ForeignKey('document.id'), nullable=False)
    
    page_number = db.Column(db.Integer, nullable=False)
    image_path = db.Column(db.String(500), nullable=False) # Path to the saved image file
    
    # Information extracted by the multimodal LLM
    name = db.Column(db.String(100), default="Unknown") # e.g., "Line Plot", "Bar Chart", "Schematic Diagram"
    description = db.Column(db.Text, nullable=True) # What the figure depicts
    analysis = db.Column(db.Text, nullable=True) # The key takeaway or conclusion from the figure
    extracted_text = db.Column(db.Text, nullable=True) # Any text OCR'd from the image (axes, labels)

    def to_dict(self):
        return {
            'id': self.id,
            'page_number': self.page_number,
            'image_path': self.image_path, # We'll need a way to serve this file
            'name': self.name,
            'description': self.description,
            'analysis': self.analysis,
            'extracted_text': self.extracted_text
        }

class Conversation(db.Model):
    """Represents a single conversation thread in a project."""
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)
    title = db.Column(db.String(150), nullable=False, default="New Conversation")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    messages = db.relationship('Message', backref='conversation', lazy='joined', cascade="all, delete-orphan", order_by='Message.timestamp')

    def __repr__(self):
        return f'<Conversation {self.title}>'

class Message(db.Model):
    """Represents a single message in a conversation."""
    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey('conversation.id'), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # 'user' or 'assistant'
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'role': self.role,
            'content': self.content,
            'timestamp': self.timestamp.isoformat()
        }
    
class Task(db.Model):
    """Represents a long-running agentic task."""
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)
    task_type = db.Column(db.String(100), nullable=False, default="report_writing")
    user_prompt = db.Column(db.Text, nullable=False)
    
    # State management
    status = db.Column(db.String(100), default="queued") # e.g., "queued", "generating_outline", "writing_section_1_of_3", "complete", "failed"
    status_message = db.Column(db.Text, nullable=True) # For storing error messages
    
    # Stored artifacts
    outline_json = db.Column(db.Text, nullable=True) # The generated outline from Step 1
    final_markdown_content = db.Column(db.Text, nullable=True) # The generated Markdown report
    final_content = db.Column(db.Text, nullable=True) # The final, fully assembled TeX report
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'project_id': self.project_id,
            'task_type': self.task_type,
            'user_prompt': self.user_prompt,
            'status': self.status,
            'status_message': self.status_message,
            'has_outline': self.outline_json is not None,
            'final_markdown_content': self.final_markdown_content,
            'has_final_report': self.final_content is not None,
            'created_at': self.created_at.isoformat()
        }