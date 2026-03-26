# generate_html.py
import os
import yaml
import json
from config import BASE_DIR, PHASES, PHASE_COLORS, CSS_STYLES, DST_HTML_DIR, OUTPUT_FILENAME

def load_yaml(filepath):
    if not os.path.exists(filepath):
        return {}
    with open(filepath, 'r') as file:
        return yaml.safe_load(file) or {}


def _as_parent_path(parent_key_spec):
    """Normalize parent key spec to a YAML path list.

    Examples:
    - "parent" -> ["parent"]
    - ["parameters", "parents"] -> ["parameters", "parents"]
    """
    if isinstance(parent_key_spec, str):
        return [parent_key_spec]
    if isinstance(parent_key_spec, (list, tuple)) and all(isinstance(k, str) for k in parent_key_spec):
        return list(parent_key_spec)
    return []


def _normalize_parent_paths(parent_key_specs):
    """Return a list of candidate YAML paths for parent lookup.

    Supported input forms:
    - ["parent"]                          -> [["parent"]]
    - ["parent", "parents"]              -> [["parent", "parents"], ["parent"], ["parents"]]
    - ["parameters", "parents"]          -> [["parameters", "parents"], ["parameters"], ["parents"]]
    - [["parameters", "parents"], "parent"] -> [["parameters", "parents"], ["parent"]]
    """
    if not isinstance(parent_key_specs, (list, tuple)):
        return []

    paths = []

    # Ambiguous list[str]: could be one nested path OR multiple top-level keys.
    if all(isinstance(item, str) for item in parent_key_specs):
        if parent_key_specs:
            paths.append(list(parent_key_specs))
        paths.extend([[item] for item in parent_key_specs])
    else:
        for spec in parent_key_specs:
            path = _as_parent_path(spec)
            if path:
                paths.append(path)

    # Deduplicate paths while preserving order.
    unique_paths = []
    seen = set()
    for path in paths:
        key = tuple(path)
        if key not in seen:
            seen.add(key)
            unique_paths.append(path)
    return unique_paths


def _get_value_by_path(data, path):
    """Traverse a YAML dict using a path of keys, returning None on missing nodes."""
    current = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _extract_parents(params, parent_key_specs):
    """Extract parent ids from configured key specs with arbitrary YAML depth."""
    parents = []
    for path in _normalize_parent_paths(parent_key_specs):

        val = _get_value_by_path(params, path)
        if val is None:
            continue

        if isinstance(val, list):
            parents.extend(val)
        elif isinstance(val, dict):
            # Ignore map-like values (e.g., selecting "parameters" node itself).
            continue
        else:
            parents.append(val)

    # Keep order while removing duplicates and null-like values.
    clean = []
    seen = set()
    for p in parents:
        if p is None:
            continue
        p = str(p)
        if p not in seen:
            seen.add(p)
            clean.append(p)
    return clean

def build_html_dashboard():
    html_columns = []
    edges_js = []
    configs_data = {}

    for i, phase in enumerate(PHASES):
        phase_name = phase["name"]
        ctrl_variants = phase.get("ctrl_variants", "variants.yaml")
        variants_file = os.path.join(BASE_DIR, phase_name, ctrl_variants)
        variants_data = load_yaml(variants_file)
        
        colors = PHASE_COLORS.get(phase_name, PHASE_COLORS["default"])
        
        column_html = f'<div class="phase-column" id="col_{phase_name}" style="border-top-color: {colors["border"]}">'
        column_html += f'<div class="phase-title">{phase_name}</div>'

        if 'variants' in variants_data:
            for variant_id, _ in variants_data['variants'].items():
                node_id = f"{phase_name}_{variant_id}"
                
                card_style = f"background-color: {colors['bg']}; border-color: {colors['border']}; color: {colors['text']};"
                # Añadidos eventos onmouseenter y onmouseleave
                column_html += f'<div class="variant-card" id="{node_id}" style="{card_style}" onclick="showConfig(\'{node_id}\')" onmouseenter="highlightLines(\'{node_id}\')" onmouseleave="resetLines()">{variant_id}</div>'
                
                params_path = os.path.join(BASE_DIR, phase_name, variant_id, "params.yaml")
                params = load_yaml(params_path)
                configs_data[node_id] = params

                parents = _extract_parents(params, phase.get("parent_keys", []))
                
                if parents and i > 0:
                    prev_phase_name = PHASES[i - 1]["name"]
                    for p in parents:
                        parent_node_id = f"{prev_phase_name}_{p}"
                        
                        # Guardamos el objeto línea junto con su origen y destino
                        edges_js.append(f"""
                            var startNode = document.getElementById('{parent_node_id}');
                            var endNode = document.getElementById('{node_id}');
                            if (startNode && endNode) {{
                                var line = new LeaderLine(startNode, endNode, {{ 
                                    color: '#adb5bd', 
                                    size: 2, 
                                    path: 'fluid', 
                                    startSocket: 'right', 
                                    endSocket: 'left'
                                }});
                                lines.push({{
                                    obj: line,
                                    source: '{parent_node_id}',
                                    target: '{node_id}'
                                }});
                            }}
                        """)
        
        column_html += '</div>'
        html_columns.append(column_html)

    html_template = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>MLOps Pipeline Lineage</title>
        <style>{CSS_STYLES}</style>
        <script src="https://cdnjs.cloudflare.com/ajax/libs/leader-line/1.0.7/leader-line.min.js"></script>
    </head>
    <body>
        <div class="pipeline-container" id="pipeline-container">
            {''.join(html_columns)}
        </div>

        <div id="config-panel">
            <span class="close-btn" onclick="closeConfig()">Cerrar ✕</span>
            <h2 id="config-title">Configuración</h2>
            <pre id="config-content">Selecciona una variante...</pre>
        </div>

        <script>
            let lines = [];
            const variantConfigs = {json.dumps(configs_data)};

            function showConfig(nodeId) {{
                document.getElementById('config-panel').classList.add('open');
                document.getElementById('config-title').innerText = "Variante: " + nodeId;
                document.getElementById('config-content').innerText = JSON.stringify(variantConfigs[nodeId], null, 2);
            }}

            function closeConfig() {{
                document.getElementById('config-panel').classList.remove('open');
            }}

            // Función auxiliar para encontrar todos los ancestros (padres, abuelos, etc.)
            function findAllAncestors(nodeId, visitedNodes) {{
                if (!visitedNodes) visitedNodes = new Set();
                if (visitedNodes.has(nodeId)) return [];
                visitedNodes.add(nodeId);
                
                let ancestors = [];
                lines.forEach(function(l) {{
                    if (l.target === nodeId && !visitedNodes.has(l.source)) {{
                        ancestors.push(l.source);
                        ancestors = ancestors.concat(findAllAncestors(l.source, visitedNodes));
                    }}
                }});
                return ancestors;
            }}

            // Función auxiliar para encontrar todos los descendientes (hijos, nietos, etc.)
            function findAllDescendants(nodeId, visitedNodes) {{
                if (!visitedNodes) visitedNodes = new Set();
                if (visitedNodes.has(nodeId)) return [];
                visitedNodes.add(nodeId);
                
                let descendants = [];
                lines.forEach(function(l) {{
                    if (l.source === nodeId && !visitedNodes.has(l.target)) {{
                        descendants.push(l.target);
                        descendants = descendants.concat(findAllDescendants(l.target, visitedNodes));
                    }}
                }});
                return descendants;
            }}

            // Función para resaltar líneas conectadas al nodo y su genealogía completa
            function highlightLines(nodeId) {{
                // Encontrar todos los ancestros y descendientes
                let ancestors = findAllAncestors(nodeId);
                let descendants = findAllDescendants(nodeId);
                let connectedNodes = new Set([nodeId, ...ancestors, ...descendants]);
                
                lines.forEach(function(l) {{
                    // Resaltar si la línea conecta nodos en la genealogía
                    if (connectedNodes.has(l.source) && connectedNodes.has(l.target)) {{
                        l.obj.color = '#ff5722'; // Naranja para resaltar
                        l.obj.size = 4;          // Más gruesa
                    }} else {{
                        l.obj.color = 'rgba(173, 181, 189, 0.1)'; // Transparente para ocultar
                    }}
                }});
            }}

            // Función para volver al estado original
            function resetLines() {{
                lines.forEach(function(l) {{
                    l.obj.color = '#adb5bd';
                    l.obj.size = 2;
                }});
            }}

            window.addEventListener('load', function() {{
                // Le damos 150ms al navegador para que dibuje el layout completo antes de trazar las líneas
                setTimeout(function() {{
                    {chr(10).join(edges_js)}
                }}, 150);
            }});

            // Listener de scroll ajustado usando requestAnimationFrame para mayor rendimiento
            document.getElementById('pipeline-container').addEventListener('scroll', function() {{
                window.requestAnimationFrame(function() {{
                    lines.forEach(function(l) {{
                        l.obj.position(); // Actualizado para llamar a .obj
                    }});
                }});
            }});

            window.addEventListener('resize', function() {{
                lines.forEach(function(l) {{
                    l.obj.position(); // Actualizado para llamar a .obj
                }});
            }});
        </script>
    </body>
    </html>
    """

    os.makedirs(DST_HTML_DIR, exist_ok=True)
    output_path = os.path.join(DST_HTML_DIR, OUTPUT_FILENAME)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_template)
    print(f"Dashboard generado: {output_path}")

if __name__ == "__main__":
    build_html_dashboard()