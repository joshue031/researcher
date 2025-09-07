from app import create_app, db
from app.models import Project, Document

app = create_app()

@app.shell_context_processor
def make_shell_context():
    """Allows for easier testing in 'flask shell'."""
    return {'db': db, 'Project': Project, 'Document': Document}

if __name__ == '__main__':
    app.run(port=5005, debug=True)
