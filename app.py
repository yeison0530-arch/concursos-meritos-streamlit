import streamlit as st
import io
import json
import PyPDF2
import unicodedata
import re
from supabase import create_client, Client
import google.generativeai as genai
import typing_extensions as typing

# --- TIPOS DE DATOS PARA GEMINI ---
class Pregunta(typing.TypedDict):
    id: int
    enunciado: str
    opciones: list[str]
    correcta: int
    justificacion: str

class TestResult(typing.TypedDict):
    preguntas: list[Pregunta]

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="Estudio Concursos", page_icon="📚", layout="wide")

# --- INICIALIZACIÓN DE SESSION STATE ---
if 'progreso_por_doc' not in st.session_state:
    st.session_state.progreso_por_doc = {} 
if 'estadisticas' not in st.session_state:
    st.session_state.estadisticas = {'total': 0, 'correctas': 0, 'incorrectas': 0}
if 'test_actual' not in st.session_state:
    st.session_state.test_actual = None
if 'respuestas_usuario' not in st.session_state:
    st.session_state.respuestas_usuario = {}

# --- CONEXIÓN A SUPABASE ---
@st.cache_resource
def init_supabase() -> Client:
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
        return create_client(url, key)
    except Exception as e:
        st.error(f"Error al conectar a Supabase. Verifica tus secrets: {e}")
        st.stop()

supabase = init_supabase()
BUCKET_NAME = 'documentos_concursos'

# --- CONFIGURACIÓN DE GEMINI ---
try:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
    model = genai.GenerativeModel("gemini-flash-latest")
except Exception as e:
    st.error(f"Error al configurar la API de Gemini: {e}")
    st.stop()

# --- FUNCIONES DE ALMACENAMIENTO Y CONCURSOS ---
def obtener_concursos():
    """Lee el archivo JSON que actúa como índice de los concursos creados."""
    try:
        res = supabase.storage.from_(BUCKET_NAME).download('concursos.json')
        return json.loads(res.decode('utf-8'))
    except Exception:
        return [] # Si no existe el archivo, devuelve lista vacía

def guardar_concursos(lista_concursos):
    """Guarda el índice de concursos actualizándolo en Supabase."""
    datos = json.dumps(lista_concursos).encode('utf-8')
    try:
        # Intentar actualizar
        supabase.storage.from_(BUCKET_NAME).update(
            file=datos,
            path='concursos.json',
            file_options={"content-type": "application/json"}
        )
        return True
    except Exception:
        # Si falla porque no existe, se sube por primera vez
        try:
            supabase.storage.from_(BUCKET_NAME).upload(
                file=datos,
                path='concursos.json',
                file_options={"content-type": "application/json"}
            )
            return True
        except Exception as e:
            st.error(f"Error de permisos en Supabase al guardar: {e}")
            return False

def crear_concurso(nombre):
    concursos = obtener_concursos()
    if nombre not in concursos:
        concursos.append(nombre)
        return guardar_concursos(concursos)
    return True

def safe_name(name):
    """Limpia tildes, espacios y caracteres especiales para usarlos en rutas de Supabase."""
    name = unicodedata.normalize('NFKD', name).encode('ASCII', 'ignore').decode('utf-8')
    name = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', name)
    return name

def eliminar_concurso(concurso):
    # Primero eliminamos todos los archivos dentro de la carpeta del concurso
    docs = listar_documentos(concurso)
    if docs:
        rutas = [f"{safe_name(concurso)}/{d}" for d in docs]
        try:
            supabase.storage.from_(BUCKET_NAME).remove(rutas)
        except Exception as e:
            st.error(f"Error al vaciar los archivos del concurso: {e}")
            
    # Luego lo eliminamos del índice
    concursos = obtener_concursos()
    if concurso in concursos:
        concursos.remove(concurso)
        guardar_concursos(concursos)

def listar_documentos(concurso):
    """Obtiene la lista de documentos en la carpeta del concurso."""
    try:
        res = supabase.storage.from_(BUCKET_NAME).list(path=safe_name(concurso))
        # Filtramos por archivos válidos
        archivos = [f['name'] for f in res if f['name'].endswith(('.pdf', '.txt'))]
        return archivos
    except Exception as e:
        # Si la carpeta no existe aún o hay un error silencioso
        return []

def subir_documento(file, concurso):
    """Sube un archivo a la carpeta del concurso en Supabase."""
    try:
        file_bytes = file.getvalue()
        ruta = f"{safe_name(concurso)}/{safe_name(file.name)}"
        res = supabase.storage.from_(BUCKET_NAME).upload(
            file=file_bytes,
            path=ruta,
            file_options={"content-type": file.type}
        )
        return True
    except Exception as e:
        st.sidebar.error(f"Error al subir el archivo (quizás ya existe): {e}")
        return False

def eliminar_documento(concurso, doc_name):
    ruta = f"{safe_name(concurso)}/{doc_name}"
    try:
        supabase.storage.from_(BUCKET_NAME).remove([ruta])
        st.sidebar.warning(f"Se ha eliminado el archivo '{doc_name}'.")
    except Exception as e:
        st.sidebar.error(f"Error al eliminar el archivo: {e}")

@st.cache_data(show_spinner=False)
def descargar_documento_cache(ruta):
    return supabase.storage.from_(BUCKET_NAME).download(ruta)

def extraer_texto_doc(concurso, doc_name):
    """Descarga el documento a la memoria y extrae su texto con barra de progreso."""
    ruta = f"{safe_name(concurso)}/{doc_name}"
    try:
        res = descargar_documento_cache(ruta)
        if doc_name.endswith('.pdf'):
            pdf_reader = PyPDF2.PdfReader(io.BytesIO(res))
            texto = ""
            total_pages = len(pdf_reader.pages)
            
            # Barra de progreso visual
            progreso_lectura = st.progress(0.0, text=f"Preparando lectura de {total_pages} páginas...")
            
            for i, page in enumerate(pdf_reader.pages):
                extracted = page.extract_text()
                if extracted:
                    texto += extracted + "\n"
                
                # Actualizar barra
                porcentaje = (i + 1) / total_pages
                progreso_lectura.progress(porcentaje, text=f"Extrayendo texto del PDF... Página {i+1} de {total_pages}")
            
            progreso_lectura.empty() # Limpiar la barra al terminar
            return texto
        elif doc_name.endswith('.txt'):
            return res.decode('utf-8')
    except Exception as e:
        st.error(f"Error al leer el documento: {e}")
        return None

# --- INTERFAZ BARRA LATERAL ---
with st.sidebar:
    st.header("🗂️ Mis Concursos")
    
    # 1. Selector de Concursos
    concursos_existentes = obtener_concursos()
    
    concurso_seleccionado = None
    if concursos_existentes:
        concurso_seleccionado = st.selectbox("Selecciona un Concurso:", concursos_existentes)
    else:
        st.info("No hay concursos. Crea uno nuevo abajo.")
        
    # Crear nuevo concurso
    with st.expander("+ Crear nuevo concurso"):
        nuevo_concurso = st.text_input("Nombre del Concurso")
        if st.button("Guardar concurso"):
            if nuevo_concurso.strip():
                if crear_concurso(nuevo_concurso.strip()):
                    st.rerun()
            else:
                st.warning("Escribe un nombre válido.")
    
    st.divider()

    # Si hay un concurso seleccionado, mostramos su gestión
    doc_seleccionado = None
    if concurso_seleccionado:
        st.header(f"📂 Fuentes de '{concurso_seleccionado}'")
        
        # Subir archivos al concurso
        uploaded_file = st.file_uploader("Sube un temario (PDF/TXT)", type=["pdf", "txt"], key="uploader")
        if uploaded_file is not None:
            if st.button("Guardar documento"):
                if subir_documento(uploaded_file, concurso_seleccionado):
                    st.success("✅ Documento guardado correctamente. Refrescando...")
                    # Pequeña pausa para que el usuario vea el mensaje
                    import time
                    time.sleep(1.5)
                    st.rerun()
        
        # Listado de archivos del concurso
        documentos = listar_documentos(concurso_seleccionado)
        
        # --- CONTADOR DE FUENTES ---
        st.write(f"**Total de fuentes en este concurso: {len(documentos)}**")
        
        if documentos:
            for doc in documentos:
                col1, col2 = st.columns([4, 1])
                col1.write(f"📄 {doc}")
                if col2.button("🗑️", key=f"del_{doc}", help="Eliminar documento"):
                    eliminar_documento(concurso_seleccionado, doc)
                    st.rerun()
            
            # Seleccionar documento para estudiar
            st.divider()
            st.write("### 👇 Comienza a estudiar aquí")
            doc_seleccionado = st.selectbox("Selecciona el documento con el que vas a generar el test:", documentos)
        else:
            st.info("No hay documentos en este concurso. Sube el primero para poder generar un test.")
            
        # Opción Peligrosa: Eliminar todo el concurso
        st.divider()
        if st.button(f"⚠️ Eliminar Concurso '{concurso_seleccionado}'"):
            eliminar_concurso(concurso_seleccionado)
            st.rerun()

# --- LÓGICA PRINCIPAL ---
st.title("📚 Creador de Tests para Concursos")

# Panel de Estadísticas en la parte superior
cols_stats = st.columns(3)
cols_stats[0].metric("Preguntas Respondidas", st.session_state.estadisticas['total'])
porcentaje_aciertos = 0
porcentaje_errores = 0
if st.session_state.estadisticas['total'] > 0:
    porcentaje_aciertos = (st.session_state.estadisticas['correctas'] / st.session_state.estadisticas['total']) * 100
    porcentaje_errores = (st.session_state.estadisticas['incorrectas'] / st.session_state.estadisticas['total']) * 100

cols_stats[1].metric("% Aciertos", f"{porcentaje_aciertos:.1f}%")
cols_stats[2].metric("% Errores", f"{porcentaje_errores:.1f}%")

st.divider()

if doc_seleccionado and concurso_seleccionado:
    # Identificador único para el progreso del documento en ese concurso
    id_progreso = f"{concurso_seleccionado}/{doc_seleccionado}"
    
    st.subheader(f"📖 Estudiando: {doc_seleccionado}")
    
    if id_progreso not in st.session_state.progreso_por_doc:
        st.session_state.progreso_por_doc[id_progreso] = 0.0
    
    progreso_actual = st.session_state.progreso_por_doc[id_progreso]
    
    st.progress(progreso_actual / 100.0, text=f"Progreso de lectura acumulado: {progreso_actual:.1f}%")
    
    porcentaje_estudio = st.slider("¿Qué porcentaje de la fuente deseas abarcar en el próximo test?", min_value=5, max_value=50, value=10, step=5)
    
    if st.button("Generar nuevo test", type="primary"):
        with st.status("Preparando tu test...", expanded=True) as status:
            st.write("📖 Extrayendo texto del documento...")
            texto_completo = extraer_texto_doc(concurso_seleccionado, doc_seleccionado)
            if texto_completo:
                total_chars = len(texto_completo)
                inicio_idx = int((progreso_actual / 100.0) * total_chars)
                fin_idx = int(((progreso_actual + porcentaje_estudio) / 100.0) * total_chars)
                fin_idx = min(fin_idx, total_chars)
                
                texto_seccion = texto_completo[inicio_idx:fin_idx]
                
                if not texto_seccion.strip():
                    status.update(label="Error de progreso", state="error", expanded=True)
                    st.warning("Ya has completado este documento o la sección no contiene texto. Reinicia tu progreso para empezar de nuevo.")
                else:
                    st.write("🤖 Analizando el texto y estructurando preguntas con IA...")
                    prompt = f"""
                    Actúa como un experto en la creación de exámenes para concursos de méritos públicos.
                    A continuación te proporciono un extracto del temario:
                    
                    --- INICIO DEL EXTRACTO ---
                    {texto_seccion}
                    --- FIN DEL EXTRACTO ---
                    
                    INSTRUCCIONES ESTRICTAS:
                    1. Genera un test con preguntas de opción múltiple (mínimo 3, máximo 5) basadas EXCLUSIVAMENTE en este texto.
                    2. Asegúrate de que haya una única opción correcta por pregunta.
                    3. La justificación debe ser detallada, pedagógica y generosa. Explica claramente por qué la opción es correcta y aporta contexto del texto para que el usuario pueda aprender profundamente sobre el tema. No te limites en la explicación, pero enfócate en enseñar.
                    4. Devuelve ÚNICAMENTE la estructura JSON solicitada, sin preámbulos ni texto adicional.
                    """
                    
                    try:
                        respuesta = model.generate_content(
                            prompt,
                            generation_config=genai.GenerationConfig(
                                response_mime_type="application/json",
                                response_schema=TestResult,
                                temperature=0.3,
                                max_output_tokens=8192 # Permitir explicaciones detalladas sin que se corte el JSON
                            )
                        )
                        st.session_state.test_actual = json.loads(respuesta.text)
                        st.session_state.respuestas_usuario = {}
                        
                        nuevo_progreso = min(progreso_actual + porcentaje_estudio, 100.0)
                        st.session_state.progreso_por_doc[id_progreso] = nuevo_progreso
                        
                        status.update(label="¡Test generado con éxito!", state="complete", expanded=False)
                        st.rerun()
                    except Exception as e:
                        status.update(label="Error en la Inteligencia Artificial", state="error", expanded=True)
                        st.error(f"Error al generar el test con la API de Gemini: {e}")

    if st.session_state.test_actual:
        st.subheader("📝 Cuestionario Actual")
        
        preguntas = st.session_state.test_actual.get("preguntas", [])
        
        for p in preguntas:
            st.markdown(f"**Pregunta {p['id']}:** {p['enunciado']}")
            
            opciones = p['opciones']
            correcta_idx = p['correcta']
            
            respondido = str(p['id']) in st.session_state.respuestas_usuario
            
            for i, opcion in enumerate(opciones):
                key = f"btn_{p['id']}_{i}"
                
                if not respondido:
                    if st.button(opcion, key=key):
                        st.session_state.respuestas_usuario[str(p['id'])] = i
                        st.session_state.estadisticas['total'] += 1
                        if i == correcta_idx:
                            st.session_state.estadisticas['correctas'] += 1
                        else:
                            st.session_state.estadisticas['incorrectas'] += 1
                        st.rerun()
                else:
                    respuesta_dada = st.session_state.respuestas_usuario[str(p['id'])]
                    if i == correcta_idx:
                        st.success(f"**✔️ {opcion}** (Respuesta Correcta)")
                    elif i == respuesta_dada and respuesta_dada != correcta_idx:
                        st.error(f"**❌ {opcion}** (Tu Respuesta)")
                    else:
                        st.markdown(f"⚪ {opcion}")
            
            if respondido:
                with st.expander("Ver Justificación", expanded=True):
                    st.info(p['justificacion'])
            st.write("---")
            
        if st.button("Repetir test actual"):
            st.session_state.respuestas_usuario = {}
            st.rerun()
else:
    st.info("👈 Por favor, selecciona o crea un concurso en la barra lateral y elige un documento para comenzar.")
