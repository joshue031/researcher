# In researcher/app/agent_logic.py

import json
import os
import subprocess
from app import db
from app.models import Task, Project
from app.llm_logic import ollama_client, get_project_paths
from flask import current_app
import faiss
import numpy as np

# --- PROMPT HELPERS ---

def _get_outline_generation_prompt(user_prompt, context_str):
    return f"""
    You are a research assistant tasked with creating a report outline.
    User's Report Description: "{user_prompt}"

    Based on the user's description and the provided information sources below, generate a report outline in LaTeX format.
    The outline should be structured to effectively answer the user's request.

    Provided Information Sources:
    {context_str}

    The JSON object must have a single key, "sections", which is an array of objects.
    Each object in the array must have two keys:
    - "title": A string for the LaTeX section title (e.g., "Introduction").
    - "description": A string detailing what this section should cover, referencing the provided sources by their Citation Key (e.g., "Aehle2024_Optimization").

    Respond with ONLY the JSON object.
    """

def _get_section_writing_prompt(user_prompt, report_context, section_title, section_description, context_str):
    return f"""
    You are a research assistant writing a single section of a technical report in GitHub Flavored Markdown.
    Your response should ONLY be the Markdown content for this section. Do not include the section header (e.g., '## Title').

    The overall goal of the report is: "{user_prompt}"

    Below is the report outline. You are currently writing the section marked with "--> YOU ARE HERE <--". 
    Previously written sections are included for context.
    --- OUTLINE CONTEXT ---
    {report_context}
    --- END OUTLINE CONTEXT ---
    
    Your current section, "{section_title}", should specifically cover: "{section_description}"

    Use the provided information sources below. You MUST cite sources using the Pandoc/Zotero format: `[@CitationKey]`. For example: `...as shown by the data [@Aehle2024].`
    
    Provided Information Sources:
    {context_str}
    """

# --- AGENT LOGIC ---

def _update_task_status(task_id, status, message=None):
    """Helper to update task status in the database."""
    task = Task.query.get(task_id)
    if task:
        task.status = status
        if message:
            task.status_message = message
        db.session.commit()

def _gather_context(project_id, user_prompt):
    """Performs KNN search on text and figures to gather relevant context."""
    project = Project.query.get(project_id)
    paths = get_project_paths(project_id)
    
    # 1. Load Text Embeddings and Mapping
    try:
        text_index = faiss.read_index(paths["index"])
        with open(paths["mapping"], 'r') as f:
            text_mapping = json.load(f)
    except FileNotFoundError:
        raise RuntimeError("Project text index not found. Please add documents.")

    # 2. Gather and Embed Figure Analyses
    figure_analyses = []
    for doc in project.documents:
        for fig in doc.figures:
            analysis_text = f"Figure named '{fig.name}' on page {fig.page_number}. Description: {fig.description}. Analysis: {fig.analysis}"
            figure_analyses.append({
                'text': analysis_text,
                'doc_id': doc.id
            })

    if figure_analyses:
        figure_embeddings = np.array([
            ollama_client.embeddings(model=current_app.config['OLLAMA_EMBEDDING_MODEL'], prompt=fig['text'])['embedding']
            for fig in figure_analyses
        ]).astype('float32')
        figure_index = faiss.IndexFlatL2(figure_embeddings.shape[1])
        figure_index.add(figure_embeddings)

    # 3. Perform KNN Search on both Text and Figures
    prompt_embedding = np.array([
        ollama_client.embeddings(model=current_app.config['OLLAMA_EMBEDDING_MODEL'], prompt=user_prompt)['embedding']
    ]).astype('float32')
    
    top_k = current_app.config['RAG_TOP_K']
    context_items = set() # Use a set to avoid duplicates

    # Search text
    _, text_indices = text_index.search(prompt_embedding, k=top_k)
    for idx in text_indices[0]:
        if str(idx) in text_mapping:
            item = text_mapping[str(idx)]
            context_items.add( (item['doc_id'], f"Relevant Text Snippet:\n{item['chunk_text']}") )

    # Search figures
    if figure_analyses:
        _, fig_indices = figure_index.search(prompt_embedding, k=top_k)
        for idx in fig_indices[0]:
            item = figure_analyses[idx]
            context_items.add( (item['doc_id'], f"Relevant Figure Analysis:\n{item['text']}") )

    # 4. Format the context string for the prompt
    context_str = ""
    doc_map = {doc.id: doc for doc in project.documents}
    for doc_id, content in context_items:
        doc = doc_map.get(doc_id)
        if not doc: continue
        
        citation_key = doc.bibtex_key 

        context_str += f"--- SOURCE START ---\n"
        context_str += f"Document Filename: {doc.filename}\n"
        context_str += f"Citation Key: {citation_key}\n\n" # Use the stored key
        context_str += f"{content}\n"
        context_str += f"--- SOURCE END ---\n\n"
        
    return context_str

def _extract_json_from_llm_response(raw_response):
    """
    Finds and extracts a JSON object from a string and fixes common errors.
    """
    # Find the first '{' and the last '}'
    start_index = raw_response.find('{')
    end_index = raw_response.rfind('}')

    if start_index != -1 and end_index != -1 and end_index > start_index:
        json_str = raw_response[start_index : end_index + 1]
        
        # Attempt to fix common errors, like unescaped backslashes in strings
        # This replaces single backslashes with double backslashes, which is valid in JSON
        json_str = json_str.replace('\\', '\\\\')
        
        return json_str
    
    return raw_response

def run_report_writing_task(task_id, app):
    """The main function for the report writing agent, designed to be run in a background thread."""
    with app.app_context():
        try:
            task = Task.query.get(task_id)
            if not task:
                print(f"Task {task_id} not found.")
                return

            # --- STEP 1: GATHER CONTEXT & GENERATE OUTLINE ---
            _update_task_status(task_id, "gathering_context")
            context_str = _gather_context(task.project_id, task.user_prompt)
            
            _update_task_status(task_id, "generating_outline")
            outline_prompt = _get_outline_generation_prompt(task.user_prompt, context_str)
            
            response = ollama_client.chat(
                model=current_app.config['OLLAMA_CHAT_MODEL'],
                messages=[{'role': 'user', 'content': outline_prompt}]
            )
            raw_llm_output = response['message']['content']
            
            # First, clean the raw output to extract only the JSON part
            cleaned_json_str = _extract_json_from_llm_response(raw_llm_output)

            print("PROMPT")
            print(outline_prompt)
            print("RESPONSE")
            print(cleaned_json_str)
            
            try:
                outline = json.loads(cleaned_json_str)
                sections = outline.get('sections', [])
                if not sections: # Check if the sections list is empty
                    raise ValueError("LLM returned valid JSON, but the 'sections' array is missing or empty.")
            except json.JSONDecodeError:
                # This is the error you are getting. We will now log the bad output.
                print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
                print("!!! FAILED TO PARSE JSON FROM LLM FOR OUTLINE GENERATION !!!")
                print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
                print(f"--- LLM Raw Output (Task {task_id}) ---")
                print(cleaned_json_str)
                print("------------------------------------")
                raise ValueError("The language model failed to return a valid JSON object for the report outline.")
            
            task.outline_json = cleaned_json_str
            db.session.commit()
            
            # --- STEP 2: WRITE REPORT SECTION BY SECTION ---
            report_body_parts = []
            generated_sections_content = {} # Store generated content
            total_sections = len(sections)

            for i, section in enumerate(sections):
                _update_task_status(task_id, f"writing_section_{i+1}_of_{total_sections}")

                # --- Build the running context ---
                report_context = ""
                for j, s in enumerate(sections):
                    if i == j:
                        report_context += f"## {s['title']}\n--> YOU ARE HERE <--\nDescription: {s['description']}\n\n"
                    elif j < i:
                        report_context += f"## {s['title']}\n{generated_sections_content[s['title']]}\n\n"
                    else:
                        report_context += f"## {s['title']}\nDescription: {s['description']}\n\n"
                # --- ---

                section_prompt = _get_section_writing_prompt(
                    task.user_prompt, report_context, section['title'], section['description'], context_str
                )
                
                response = ollama_client.chat(
                    model=current_app.config['OLLAMA_CHAT_MODEL'],
                    messages=[{'role': 'user', 'content': section_prompt}]
                )
                section_content = response['message']['content']
                
                # Assemble the TeX for this section
                generated_sections_content[section['title']] = section_content
                report_body_parts.append(f"## {section['title']}\n{section_content}\n")

            # --- STEP 3: ASSEMBLE FINAL REPORT ---
            _update_task_status(task_id, "assembling_report")
            
            # 1. Assemble the final Markdown content
            full_markdown_content = "\n".join(report_body_parts)
            task.final_markdown_content = full_markdown_content
            db.session.commit() # Save the markdown to the DB

            # 2. Create a temporary .bib file for this project
            project_bib_content = "\n\n".join([doc.bibtex_full_entry for doc in task.project.documents])
            project_dir = get_project_paths(task.project_id)['index'].rsplit('/', 1)[0]
            bib_filepath = os.path.join(project_dir, f"task_{task_id}_references.bib")
            with open(bib_filepath, 'w') as f:
                f.write(project_bib_content)

            # 3. Call Pandoc to convert Markdown+BibTeX to LaTeX
            # The command tells pandoc to use the bib file and generate citations
            command = [
                'pandoc',
                '--from', 'markdown',
                '--to', 'latex',
                '--bibliography', bib_filepath,
                '--citeproc' # This processes the [@key] citations
            ]
            
            result = subprocess.run(
                command, 
                input=full_markdown_content, 
                capture_output=True, 
                text=True,
                check=True # Raise an error if pandoc fails
            )
            
            final_latex_content = result.stdout
            
            task.final_content = final_latex_content
            _update_task_status(task_id, "complete")

            # Clean up the temp bib file
            #os.remove(bib_filepath)

        except Exception as e:
            print(f"Task {task_id} failed: {e}")
            _update_task_status(task_id, "failed", str(e))
