# config.py

BASE_DIR = "executions"
DST_HTML_DIR = "executions"
OUTPUT_FILENAME = "pipeline_lineage.html"

# f01_explore  f03_windows  f05_modeling  f07_modval  pipeline_lineage.html
# f02_events   f04_targets  f06_quant     f08_sysval

PHASES = [
    {"name": "f01_explore",  "ctrl_variants": "variants.yaml",  "parent_keys": []},
    {"name": "f02_events",   "ctrl_variants": "variants.yaml",  "parent_keys": ["parent"]},
    {"name": "f03_windows",  "ctrl_variants": "variants.yaml",  "parent_keys": ["parameters","parent_variant"]},
    {"name": "f04_targets",  "ctrl_variants": "variants.yaml",  "parent_keys": ["parameters","parent_variant"]},
    {"name": "f05_modeling", "ctrl_variants": "variants.yaml",  "parent_keys": ["parameters","parent_variant"]},
    {"name": "f06_quant",    "ctrl_variants": "variants.yaml",  "parent_keys": ["parameters","parent_variant"]},
    {"name": "f07_modval",   "ctrl_variants": "variants.yaml",  "parent_keys": ["parameters","parent_variant"]},
    {"name": "f08_sysval",   "ctrl_variants": "variants.yaml",  "parent_keys": ["parameters","parents"]},
]

PHASE_COLORS = {
    "f01_explore":           {"bg": "#E3F2FD", "border": "#90CAF9", "text": "#1565C0"},
    "f02_events":   {"bg": "#E8F5E9", "border": "#A5D6A7", "text": "#2E7D32"},
    "f03_windows":  {"bg": "#FFF3E0", "border": "#FFCC80", "text": "#EF6C00"},
    "f04_targets": {"bg": "#F3E5F5", "border": "#CE93D8", "text": "#6A1B9A"},
    "f05_modeling":          {"bg": "#FFEBEE", "border": "#EF9A9A", "text": "#C62828"},
    "f06_quant":         {"bg": "#E0F7FA", "border": "#80DEEA", "text": "#006064"},
    "f07_modval":         {"bg": "#ECEFF1", "border": "#B0BEC5", "text": "#37474F"},
    "f08_sysval":       {"bg": "#F9FBE7", "border": "#E6EE9C", "text": "#827717"},
    "default":              {"bg": "#FFFFFF", "border": "#CCCCCC", "text": "#333333"}
}

CSS_STYLES = """
    /* Reseteamos el body para que no genere scroll extra */
    body { 
        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; 
        background-color: #f8f9fa; 
        margin: 0; 
        padding: 0; 
        overflow: hidden; /* Oculta el scrollbar general de la ventana */
        height: 100vh;
        width: 100vw;
    }
    
    /* El contenedor es el único que hace scroll */
    .pipeline-container { 
        display: flex; 
        flex-direction: row; 
        gap: 80px; 
        overflow: auto; /* Único scrollbar aquí */
        padding: 40px; 
        box-sizing: border-box;
        height: 100%;
        align-items: flex-start; 
    }
    
    .phase-column { 
        display: flex; 
        flex-direction: column; 
        gap: 30px; 
        min-width: 220px; 
        flex-shrink: 0; /* EVITA QUE LAS CAJAS SE APLASTEN O DESAPAREZCAN */
        background: #fff; 
        padding: 15px; 
        border-radius: 10px; 
        box-shadow: 0 2px 4px rgba(0,0,0,0.05); 
        border-top: 4px solid #dee2e6;
        position: relative;
        z-index: 1;
    }
    
    .phase-title { text-align: center; color: #343a40; font-size: 1rem; margin-bottom: 10px; border-bottom: 2px solid #e9ecef; padding-bottom: 10px; text-transform: uppercase; font-weight: bold; }
    
    .variant-card {
        border-width: 2px; border-style: solid; border-radius: 8px; padding: 15px;
        cursor: pointer; transition: all 0.2s ease; text-align: center; font-weight: 600;
        position: relative; z-index: 2; 
    }
    .variant-card:hover { transform: translateY(-3px); box-shadow: 0 6px 12px rgba(0,0,0,0.1); filter: brightness(0.95); }
    
    #config-panel {
        position: fixed; top: 0; right: -450px; width: 400px; height: 100vh; background: white;
        box-shadow: -4px 0 15px rgba(0,0,0,0.1); transition: right 0.3s ease; padding: 20px; overflow-y: auto; z-index: 10;
        box-sizing: border-box;
    }
    #config-panel.open { right: 0; }
    .close-btn { cursor: pointer; color: red; float: right; font-weight: bold; }
    pre { background: #f1f3f5; padding: 10px; border-radius: 5px; overflow-x: auto; font-size: 0.85rem; }
"""