import os
import json
import ollama
import numpy as np
import faiss
import fitz

from flask import current_app
from app import db
from app.models import Project, Document, Conversation, Message, Figure
from app.prompts import get_metadata_extraction_prompt, get_rag_prompt, get_figure_analysis_prompt
from app.utils import load_and_split_document

# Initialize the Ollama client
ollama_client = ollama.Client()

def get_project_paths(project_id):
    """Constructs paths for a project's data files."""
    project_dir = os.path.join(current_app.config['PROJECTS_DATA_DIR'], str(project_id))
    os.makedirs(project_dir, exist_ok=True)
    return {
        "index": os.path.join(project_dir, 'docs.index'),
        "mapping": os.path.join(project_dir, 'mapping.json')
    }

def process_and_embed_document(project_id, file_path, doc_type, filename):
    """The main workflow for adding a new document."""
    # 1. Load document and split into chunks
    chunks = load_and_split_document(file_path)
    if not chunks:
        raise ValueError("Could not read or process the document.")

    # 2. LLM call to extract metadata fields as JSON
    full_text = " ".join(chunks)
    metadata_prompt = get_metadata_extraction_prompt(full_text, doc_type)
    
    try:
        response = ollama_client.chat(
            model=current_app.config['OLLAMA_CHAT_MODEL'],
            messages=[{"role": "user", "content": metadata_prompt}]
        )
        metadata = json.loads(response['message']['content'])
    except Exception as e:
        raise RuntimeError(f"Failed to get metadata from LLM: {e}")
    
    print(response)

    # 3. Construct BibTeX entry from the extracted metadata in Python
    title = metadata.get('title', 'Untitled')
    author = metadata.get('author', '')
    year = metadata.get('year', '')
    
    # Generate a simple BibTeX key (e.g., Smith2024_Article)
    author_lastname = author.split(' ')[0].capitalize() if author else 'Unknown'
    title_firstword = title.split(' ')[0].capitalize() if title else 'Title'
    bib_key = f"{author_lastname}{year}_{title_firstword}"

    # Construct the full BibTeX entry string
    full_bibtex_entry = ""
    if doc_type == 'journal_article':
        journal = metadata.get('journal', '')
        volume = metadata.get('volume', '')
        pages = metadata.get('pages', '')
        full_bibtex_entry = (f"@article{{{bib_key},\n"
                             f"    author  = \"{author}\",\n"
                             f"    title   = \"{title}\",\n"
                             f"    journal = \"{journal}\",\n"
                             f"    year    = \"{year}\",\n"
                             f"    volume  = \"{volume}\",\n"
                             f"    pages   = \"{pages}\"\n"
                             f"}}")
    else:
        howpublished = metadata.get('howpublished', '')
        full_bibtex_entry = (f"@misc{{{bib_key},\n"
                             f"    author  = \"{author}\",\n"
                             f"    title   = \"{title}\",\n"
                             f"    year    = \"{year}\",\n"
                             f"    howpublished = \"{howpublished}\"\n"
                             f"}}")

    # 4. Store metadata in the main SQL database
    new_doc = Document(
        project_id=project_id,
        filename=filename,
        document_type=doc_type,
        title=title,
        description=metadata.get('description', ''),
        bibtex_key=bib_key,
        bibtex_author=author,
        bibtex_year=year,
        bibtex_full_entry=full_bibtex_entry
    )
    db.session.add(new_doc)
    db.session.commit()

    # 5. After saving the document, start the figure analysis if it's a PDF
    if filename.lower().endswith('.pdf'):
        try:
            extract_and_analyze_figures(new_doc.id, project_id, file_path)
        except Exception as e:
            # We don't want figure extraction failure to stop the whole process
            print(f"An error occurred during figure processing: {e}")

    # 6. Create and store embeddings in FAISS
    try:
        embeddings = [
            ollama_client.embeddings(model=current_app.config['OLLAMA_EMBEDDING_MODEL'], prompt=chunk)['embedding']
            for chunk in chunks
        ]
    except Exception as e:
        raise RuntimeError(f"Failed to create embeddings with Ollama: {e}")

    update_faiss_index(project_id, new_doc.id, chunks, embeddings)
    
    # We need to return the full document details for the UI to update correctly
    return {
        'id': new_doc.id,
        'title': new_doc.title,
        'description': new_doc.description,
        'filename': new_doc.filename, # It's good practice to return this too
        'bibtex_data': {
            'key': new_doc.bibtex_key,
            'author': new_doc.bibtex_author,
            'year': new_doc.bibtex_year,
            'full_entry': new_doc.bibtex_full_entry
        }
    }

def update_faiss_index(project_id, doc_id, chunks, embeddings):
    """Adds new document embeddings to the project's FAISS index."""
    paths = get_project_paths(project_id)
    index_path = paths["index"]
    mapping_path = paths["mapping"]

    # Load existing index and mapping if they exist
    if os.path.exists(index_path):
        index = faiss.read_index(index_path)
        with open(mapping_path, 'r') as f:
            mapping = json.load(f)
    else:
        dimension = len(embeddings[0])
        index = faiss.IndexFlatL2(dimension) # Using L2 distance for similarity
        mapping = {}

    # Add new embeddings to index and update mapping
    start_index = index.ntotal
    index.add(np.array(embeddings, dtype=np.float32))
    for i, chunk in enumerate(chunks):
        mapping[str(start_index + i)] = {'doc_id': doc_id, 'chunk_text': chunk}
        
    # Save updated index and mapping
    faiss.write_index(index, index_path)
    with open(mapping_path, 'w') as f:
        json.dump(mapping, f)

def rebuild_faiss_index_for_project(project_id):
    """
    Deletes the old FAISS index and rebuilds it from scratch for a given project.
    This is the simplest way to handle deletions from a FAISS index.
    """
    paths = get_project_paths(project_id)
    index_path = paths["index"]
    mapping_path = paths["mapping"]

    # Delete old index files
    if os.path.exists(index_path):
        os.remove(index_path)
    if os.path.exists(mapping_path):
        os.remove(mapping_path)
    
    # Get all remaining documents for the project
    project = Project.query.get(project_id)
    if not project.documents:
        # If no documents are left, we're done.
        return

    # Re-process and re-embed all remaining documents
    print(f"Rebuilding index for project {project_id}...")
    for doc in project.documents:
        project_dir = os.path.join(current_app.config['PROJECTS_DATA_DIR'], str(project.id))
        file_path = os.path.join(project_dir, doc.filename)

        if not os.path.exists(file_path):
            print(f"Warning: File {doc.filename} not found for re-indexing. Skipping.")
            continue

        chunks = load_and_split_document(file_path)
        if not chunks:
            continue
        
        try:
            embeddings = [
                ollama_client.embeddings(model=current_app.config['OLLAMA_EMBEDDING_MODEL'], prompt=chunk)['embedding']
                for chunk in chunks
            ]
            update_faiss_index(project_id, doc.id, chunks, embeddings)
        except Exception as e:
            print(f"Error re-embedding document {doc.id}: {e}")
    print("Index rebuild complete.")

def answer_question(project_id, question):
    """Answers a question using the RAG workflow."""
    paths = get_project_paths(project_id)
    index_path = paths["index"]
    mapping_path = paths["mapping"]

    if not os.path.exists(index_path):
        return "This project has no documents to search."

    # 1. Load index and mapping
    index = faiss.read_index(index_path)
    with open(mapping_path, 'r') as f:
        mapping = json.load(f)

    # 2. Create an embedding for the user's question
    try:
        question_embedding = ollama_client.embeddings(
            model=current_app.config['OLLAMA_EMBEDDING_MODEL'],
            prompt=question
        )['embedding']
    except Exception as e:
        raise RuntimeError(f"Failed to embed question: {e}")

    # 3. Perform KNN search on the FAISS index
    k = min(current_app.config['RAG_TOP_K'], index.ntotal)
    query_vector = np.array([question_embedding], dtype=np.float32)
    distances, indices = index.search(query_vector, k)

    # 4. Retrieve the relevant text chunks
    relevant_chunks = [mapping[str(i)]['chunk_text'] for i in indices[0]]
    context = "\n---\n".join(relevant_chunks)

    # 5. Generate an answer using the main LLM with the retrieved context
    rag_prompt = get_rag_prompt(question, context)
    print("RAG PROMPT")
    print(rag_prompt)
    try:
        response = ollama_client.chat(
            model=current_app.config['OLLAMA_CHAT_MODEL'],
            messages=[{"role": "user", "content": rag_prompt}]
        )
        print("\n\nRESPONSE")
        print(response)
        return response['message']['content']
    except Exception as e:
        raise RuntimeError(f"Failed to generate answer with LLM: {e}")

def extract_and_analyze_figures(document_id, project_id, pdf_path):
    """
    Finds regions of figures using a multi-pass approach to include associated text, 
    renders them as images, analyzes them with a multimodal LLM, and saves the results.
    """
    print(f"Starting advanced figure extraction for document {document_id}...")
    pdf_document = fitz.open(pdf_path)
    project_dir = os.path.join(current_app.config['PROJECTS_DATA_DIR'], str(project_id))
    figures_dir = os.path.join(project_dir, 'figures')
    os.makedirs(figures_dir, exist_ok=True)
    
    analysis_prompt = get_figure_analysis_prompt()

    for page_num in range(len(pdf_document)):
        page = pdf_document.load_page(page_num)

        # 1. Pass 1: Find and merge the core GRAPHICAL elements first.
        image_bboxes = [img_info['bbox'] for img_info in page.get_image_info()]
        drawing_bboxes = [(path['rect'].x0, path['rect'].y0, path['rect'].x1, path['rect'].y1) for path in page.get_drawings()]
        graphic_bboxes = image_bboxes + drawing_bboxes
        core_figure_regions = merge_nearby_bboxes(graphic_bboxes, threshold=15)

        if not core_figure_regions:
            continue # No graphical elements found on this page

        # 2. Pass 2: Find TEXT blocks associated with these core regions.
        final_figure_components = list(core_figure_regions)
        text_blocks = page.get_text("blocks") # Get all text blocks with their bboxes

        for region_bbox in core_figure_regions:
            # Create an "inflation zone" around the core graphic to search for text
            inflation_pixels = 50 # How far to search for text, can be tuned
            search_zone = fitz.Rect(region_bbox) + (-inflation_pixels, -inflation_pixels, inflation_pixels, inflation_pixels)
            
            for tb in text_blocks:
                text_bbox = fitz.Rect(tb[:4])
                # If a text block intersects with our search zone, we assume it's part of the figure
                if search_zone.intersects(text_bbox):
                    final_figure_components.append(tb[:4])

        # 3. Pass 3: Run a FINAL merge on the combined list of graphics and text.
        final_figure_bboxes = merge_nearby_bboxes(final_figure_components, threshold=25)

        for i, bbox in enumerate(final_figure_bboxes):

            # Use our smart filter BEFORE doing any expensive work
            if not is_likely_figure(page, bbox, text_blocks, image_bboxes, drawing_bboxes):
                continue # Skip this bounding box entirely

            figure_rect = fitz.Rect(bbox)

            # Size filters are mostly handled by is_likely_figure, but we can keep a basic one
            if figure_rect.is_empty or figure_rect.width < 50 or figure_rect.height < 50:
                 continue

            # Render the final, correctly-sized region as a high-res image
            pix = page.get_pixmap(clip=figure_rect, dpi=300)
            
            image_filename = f"doc_{document_id}_p{page_num + 1}_fig{i}.png"
            image_path = os.path.join(figures_dir, image_filename)
            pix.save(image_path)

            print(f"  > Analyzing composite figure: {image_filename}")
            try:
                # Analyze the rendered figure with the multimodal model
                response = ollama_client.chat(
                    model='gemma3:27b',
                    messages=[{
                        'role': 'user',
                        'content': analysis_prompt,
                        'images': [image_path]
                    }],
                    format='json'
                )
                # print(response)
                analysis_data = json.loads(response['message']['content'])

                new_figure = Figure(
                    document_id=document_id,
                    page_number=page_num + 1,
                    image_path=os.path.join('figures', image_filename),
                    name=analysis_data.get('name', 'Unknown'),
                    description=analysis_data.get('description'),
                    analysis=analysis_data.get('analysis'),
                    extracted_text=analysis_data.get('extracted_text')
                )
                db.session.add(new_figure)
                db.session.commit()

            except Exception as e:
                print(f"    ! Error analyzing figure {image_filename}: {e}")
                if os.path.exists(image_path):
                    os.remove(image_path)
    
    print("Advanced figure extraction and analysis complete.")

def merge_nearby_bboxes(bboxes, threshold=20):
    """
    Merges bounding boxes that are close to each other into a single bounding box.
    This is the key to combining fragmented figure parts.
    """
    if not bboxes:
        return []
        
    # Convert bboxes to fitz.Rect objects for easier manipulation
    rects = [fitz.Rect(bbox) for bbox in bboxes]
    
    merged = True
    while merged:
        merged = False
        for i in range(len(rects) - 1, -1, -1):
            for j in range(i - 1, -1, -1):
                r1 = rects[i]
                r2 = rects[j]
                
                # Create slightly inflated rects to check for proximity
                r1_inflated = r1 + (-threshold, -threshold, threshold, threshold)
                
                if r1_inflated.intersects(r2):
                    # Merge the two rectangles by including both
                    merged_rect = r1 | r2  # The '|' operator is union
                    rects[j] = merged_rect
                    rects.pop(i)
                    merged = True
                    break
            if merged:
                break
                
    return [tuple(r) for r in rects]

def is_likely_figure(page, bbox, text_blocks, image_bboxes, drawing_bboxes):
    """
    Applies a set of heuristics to determine if a given bounding box is likely a figure.
    """
    figure_rect = fitz.Rect(bbox)
    
    # Heuristic 1: Filter by size and aspect ratio
    # Discard very small regions
    if figure_rect.width < 50 or figure_rect.height < 50:
        return False
    # Discard extreme aspect ratios (likely lines or banners)
    aspect_ratio = figure_rect.width / figure_rect.height if figure_rect.height > 0 else 0
    if aspect_ratio > 10 or aspect_ratio < 0.1:
        return False

    # Heuristic 2: Check for "Rich Content" (mix of graphics and text)
    # Find all elements that are *inside* our candidate bounding box
    contained_images = any(figure_rect.intersects(fitz.Rect(b)) for b in image_bboxes)
    contained_drawings = any(figure_rect.intersects(fitz.Rect(b)) for b in drawing_bboxes)
    contained_graphics = contained_images or contained_drawings

    # If there are no graphical elements, it's definitely not a figure.
    if not contained_graphics:
        return False
        
    # Heuristic 3: Calculate Text Density
    total_text_area = 0
    total_text_len = 0
    for tb in text_blocks:
        text_rect = fitz.Rect(tb[:4])
        if figure_rect.intersects(text_rect): # Only consider text inside the bbox
            total_text_area += text_rect.width * text_rect.height
            total_text_len += len(tb[4])

    figure_area = figure_rect.width * figure_rect.height
    if figure_area == 0:
        return False
        
    # Calculate the ratio of the figure's area that is covered by text blocks
    text_coverage_ratio = total_text_area / figure_area
    
    # If the region is > 70% text blocks by area, it's probably not a figure.
    if text_coverage_ratio > 0.7:
        return False

    # Optional Heuristic 4: A figure must contain SOME graphics.
    # We already checked this with `contained_graphics`. If we wanted to be stricter,
    # we could also check that the graphics area is a significant portion of the total area.

    # If it passes all checks, it's likely a figure.
    return True