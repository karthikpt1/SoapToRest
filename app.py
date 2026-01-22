import streamlit as st
from zeep import Client
import yaml
import json
import os
import shutil
import tempfile

# --- 1. CORE LOGIC: XSD to JSON Schema ---

def map_xsd_to_json_type(xsd_type_name):
    """Maps XSD primitive types to JSON schema types."""
    xtype = str(xsd_type_name).lower()
    if any(x in xtype for x in ["int", "long", "short", "integer"]):
        return {"type": "integer"}
    elif any(x in xtype for x in ["decimal", "float", "double", "number"]):
        return {"type": "number"}
    elif "boolean" in xtype:
        return {"type": "boolean"}
    elif "datetime" in xtype or "date" in xtype:
        return {"type": "string", "format": "date-time"}
    return {"type": "string"}

def zeep_type_to_json_schema(zeep_type):
    """Recursively converts Zeep type objects to JSON Schema with attribute support."""
    if not zeep_type:
        return {"type": "object", "properties": {}}

    # Extract children and attributes
    elements = getattr(zeep_type, 'elements', [])
    attributes = getattr(zeep_type, 'attributes', [])

    # CASE A: PURE PRIMITIVE
    if not elements and not attributes:
        return map_xsd_to_json_type(zeep_type)

    # CASE B: COMPLEX TYPE
    properties = {}
    required = []

    try:
        # 1. Map XML Attributes
        for attr_name, attr_obj in attributes:
            properties[attr_name] = map_xsd_to_json_type(attr_obj.type)

        # 2. Map Child Elements
        for name, element in elements:
            child_schema = zeep_type_to_json_schema(element.type)

            if element.max_occurs and (element.max_occurs == 'unbounded' or (isinstance(element.max_occurs, int) and element.max_occurs > 1)):
                properties[name] = {"type": "array", "items": child_schema}
            else:
                properties[name] = child_schema
            
            if element.min_occurs and int(element.min_occurs) > 0:
                required.append(name)
        
        # 3. SimpleContent Handle
        if attributes and not elements:
            properties["_value"] = {"type": "string", "description": "The text content of the element"}

    except Exception:
        return {"type": "string"}

    schema = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema

# --- 2. SESSION STATE MANAGEMENT ---

if 'step' not in st.session_state:
    st.session_state.step = 'upload'
if 'op_data' not in st.session_state:
    st.session_state.op_data = {}  # Stores parsed operations
if 'final_spec' not in st.session_state:
    st.session_state.final_spec = None

def restart_process():
    st.session_state.step = 'upload'
    st.session_state.op_data = {}
    st.session_state.final_spec = None

# --- 3. STREAMLIT UI ---

st.set_page_config(page_title="WSDL to Swagger Pro", layout="wide")

# Sidebar for all inputs
with st.sidebar:
    st.header("⚙️ Utility Configuration")
    
    if st.button("🔄 Start New Project"):
        restart_process()
        st.rerun()
        
    uploaded_files = st.file_uploader(
        "Upload WSDL & XSD Files", 
        type=['wsdl', 'xsd', 'xml'], 
        accept_multiple_files=True,
        help="Upload the main WSDL and any secondary XSD files it imports."
    )
    st.divider()

# --- STEP 1: FILE UPLOAD & PARSING ---
if uploaded_files and st.session_state.step == 'upload':
    temp_dir = tempfile.mkdtemp()
    wsdl_files = []
    
    for f in uploaded_files:
        path = os.path.join(temp_dir, f.name)
        with open(path, "wb") as buffer:
            buffer.write(f.getbuffer())
        if f.name.endswith(".wsdl"):
            wsdl_files.append(f.name)

    if not wsdl_files:
        st.warning("⚠️ No .wsdl file detected. Please upload at least one WSDL.")
    else:
        main_wsdl = st.selectbox("Select Main WSDL Entry Point:", wsdl_files)
        
        if st.button("Parse Operations & Design Schema"):
            try:
                main_path = os.path.join(temp_dir, main_wsdl)
                client = Client(main_path)
                
                extracted_ops = {}
                for service in client.wsdl.services.values():
                    for port in service.ports.values():
                        for op in port.binding._operations.values():
                            req_type = op.input.body.type if op.input and op.input.body else None
                            res_type = op.output.body.type if op.output and op.output.body else None
                            
                            extracted_ops[op.name] = {
                                "request": zeep_type_to_json_schema(req_type),
                                "response": zeep_type_to_json_schema(res_type),
                                "include": True,
                                "tag": service.name
                            }
                
                st.session_state.op_data = extracted_ops
                st.session_state.step = 'edit'
                st.rerun()
                
            except Exception as e:
                st.error(f"🚨 Processing Error: {e}")
            finally:
                shutil.rmtree(temp_dir)

# --- STEP 2: INTERMEDIATE EDITING ---
elif st.session_state.step == 'edit':
    st.header("🛠️ Step 2: Refine API Operations")
    st.info("Edit the JSON schemas below to add/remove elements. Uncheck 'Include' to exclude an operation from the final Swagger.")
    
    current_data = st.session_state.op_data
    updated_data = {}

    for op_name, info in current_data.items():
        with st.expander(f"Operation: {op_name}", expanded=True):
            col_inc, col_req, col_res = st.columns([1, 4, 4])
            
            with col_inc:
                is_included = st.checkbox("Include", value=info['include'], key=f"check_{op_name}")
            
            with col_req:
                st.markdown("**Request Schema**")
                req_json = st.text_area("JSON", value=json.dumps(info['request'], indent=2), height=250, key=f"req_{op_name}", label_visibility="collapsed")
            
            with col_res:
                st.markdown("**Response Schema**")
                res_json = st.text_area("JSON", value=json.dumps(info['response'], indent=2), height=250, key=f"res_{op_name}", label_visibility="collapsed")
            
            try:
                updated_data[op_name] = {
                    "request": json.loads(req_json),
                    "response": json.loads(res_json),
                    "include": is_included,
                    "tag": info['tag']
                }
            except Exception as e:
                st.error(f"Error in {op_name} JSON: {e}")
                updated_data[op_name] = info

    if st.button("Generate Swagger UI 🚀"):
        # Construct Final OpenAPI Spec
        openapi_spec = {
            "openapi": "3.0.0",
            "info": {
                "title": "Restructured REST API", 
                "version": "1.0.0",
                "description": "Auto-generated REST facade from SOAP WSDL."
            },
            "paths": {}
        }

        for name, data in updated_data.items():
            if data['include']:
                openapi_spec["paths"][f"/{name}"] = {
                    "post": {
                        "tags": [data['tag']],
                        "summary": name,
                        "requestBody": {
                            "content": {"application/json": {"schema": data['request']}}
                        },
                        "responses": {
                            "200": {
                                "description": "Success",
                                "content": {"application/json": {"schema": data['response']}}
                            }
                        }
                    }
                }
        
        st.session_state.final_spec = openapi_spec
        st.session_state.step = 'visualize'
        st.rerun()

# --- STEP 3: FINAL VISUALIZATION ---
elif st.session_state.step == 'visualize':
    st.header("✨ Step 3: Final API Design")
    
    if st.button("⬅️ Back to Editor"):
        st.session_state.step = 'edit'
        st.rerun()

    json_spec = json.dumps(st.session_state.final_spec, indent=2)
    st.sidebar.subheader("💾 Export Results")
    st.sidebar.download_button("Download OpenAPI JSON", json_spec, "swagger.json", mime="application/json")

    # --- Full Width Swagger UI (Original Styling) ---
    st.components.v1.html(f"""
        <div id="swagger-ui"></div>
        <script src="https://unpkg.com/swagger-ui-dist@3/swagger-ui-bundle.js"></script>
        <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@3/swagger-ui.css" />
        <style>
            :root {{ color-scheme: light !important; }}
            html, body {{
                background-color: white !important;
                color: black !important;
                margin: 0;
                padding: 0;
            }}
            #swagger-ui {{ background-color: white !important; }}
            .swagger-ui {{ filter: none !important; }}
        </style>
        <script>
            window.onload = function() {{
                SwaggerUIBundle({{
                    spec: {json_spec},
                    dom_id: '#swagger-ui',
                    deepLinking: true,
                    presets: [SwaggerUIBundle.presets.apis],
                }});
            }};
        </script>
    """, height=1000, scrolling=True)

# --- INITIAL INFO SCREEN ---
else:
    st.info("👈 **Welcome! Please upload your WSDL and any associated XSD files in the sidebar to begin.**")
    st.markdown("""
    ### How to use this utility:
    1. **Upload Files:** Use the file uploader on the left.
    2. **Resolve Dependencies:** If your WSDL imports external schemas, upload them all at once.
    3. **Select WSDL:** Pick the main entry point and click 'Parse'.
    4. **Refine Design:** (New) Add, remove, or modify JSON elements for your REST endpoints.
    5. **Preview:** The Swagger UI will generate based on your custom modifications.
    6. **Download:** Export your final OpenAPI 3.0 specification from the sidebar.
    """)