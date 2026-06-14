import streamlit as st
import io
import json
import PyPDF2
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
    model = genai.GenerativeModel("gemini-1.5-flash")
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
    except Exception:
        # Si falla porque no existe, se sube por primera vez
        try:
            supabase.storage.from_(BUCKET_NAME).upload(
                file=datos,
                path='concursos.json',
                file_options={"content-type": "application/json"}
            )
        except Exception as e:
            st.error(f"Error al guardar lista de concursos: {e}")

def crear_concurso(nombre):
    concursos = obtener_concursos()
    if nombre not in concursos:
        concursos.append(nombre)
        guardar_concursos(concursos)

def eliminar_concurso(concurso):
    # Primero eliminamos todos los archivos dentro de la carpeta del concurso
    docs = listar_documentos(concurso)
    if docs:
        rutas = [f"{concurso}/{d}" for d in docs]
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
        res = supabase.storage.from_(BUCKET_NAME).list(path=concurso)
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
        ruta = f"{concurso}/{file.name}"
        res = supabase.storage.from_(BUCKET_NAME).upload(
            file=file_bytes,
            path=ruta,
            file_options={"content-type": file.type}
        )
        st.sidebar.success(f"Archivo guardado en el concurso '{concurso}'.")
    except Exception as e:
        st.sidebar.error(f"Error al subir el archivo (quizás ya existe): {e}")

def eliminar_documento(concurso, doc_name):
    ruta = f"{concurso}/{doc_name}"
    try:
        supabase.storage.from_(BUCKET_NAME).remove([ruta])
        st.sidebar.warning(f"Se ha eliminado el archivo '{doc_name}'.")
    except Exception as e:
        st.sidebar.error(f"Error al eliminar el archivo: {e}")

def extraer_texto_doc(concurso, doc_name):
    """Descarga el documento a la memoria y extrae su texto."""
    ruta = f"{concurso}/{doc_name}"
    try:
        res = supabase.storage.from_(BUCKET_NAME).download(ruta)
        if doc_name.endswith('.pdf'):
            pdf_reader = PyPDF2.PdfReader(io.BytesIO(res))
            texto = ""
            for page in pdf_reader.pages:
                extracted = page.extract_text()
                if extracted:
                    texto += extracted + "\n"
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
                crear_concurso(nuevo_concurso.strip())
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
                subir_documento(uploaded_file, concurso_seleccionado)
                st.rerun()
        
        # Listado de archivos del concurso
        documentos = listar_documentos(concurso_seleccionado)
        if documentos:
            st.write("**Documentos guardados:**")
            for doc in documentos:
                col1, col2 = st.columns([4, 1])
                col1.write(f"📄 {doc}")
                if col2.button("🗑️", key=f"del_{doc}", help="Eliminar documento"):
                    eliminar_documento(concurso_seleccionado, doc)
                    st.rerun()
            
            # Seleccionar documento para estudiar
            st.divider()
            doc_seleccionado = st.selectbox("Selecciona el documento para estudiar:", documentos)
        else:
            st.info("No hay documentos en este concurso. Sube el primero.")
            
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
        with st.spinner("Leyendo la fuente y generando preguntas con Inteligencia Artificial..."):
            texto_completo = extraer_texto_doc(concurso_seleccionado, doc_seleccionado)
            if texto_completo:
                total_chars = len(texto_completo)
                inicio_idx = int((progreso_actual / 100.0) * total_chars)
                fin_idx = int(((progreso_actual + porcentaje_estudio) / 100.0) * total_chars)
                fin_idx = min(fin_idx, total_chars)
                
                texto_seccion = texto_completo[inicio_idx:fin_idx]
                
                if not texto_seccion.strip():
                    st.warning("Ya has completado este documento o la sección no contiene texto. Reinicia tu progreso para empezar de nuevo.")
                else:
                    prompt = f"""
                    Actúa como un Desarrollador Senior y experto en la creación de exámenes para concursos de méritos públicos.
                    A continuación te proporciono un extracto del temario:
                    
                    --- INICIO DEL EXTRACTO ---
                    {texto_seccion}
                    --- FIN DEL EXTRACTO ---
                    
                    Genera un test con preguntas de opción múltiple (mínimo 3, máximo 5) basadas EXCLUSIVAMENTE en este texto.
                    Asegúrate de que haya una única opción correcta por pregunta.
                    La justificación debe explicar de forma clara por qué la opción correcta lo es, basándose en la información del texto.
                    """
                    
                    try:
                        respuesta = model.generate_content(
                            prompt,
                            generation_config=genai.GenerationConfig(
                                response_mime_type="application/json",
                                response_schema=TestResult,
                                temperature=0.2
                            )
                        )
                        st.session_state.test_actual = json.loads(respuesta.text)
                        st.session_state.respuestas_usuario = {}
                        
                        nuevo_progreso = min(progreso_actual + porcentaje_estudio, 100.0)
                        st.session_state.progreso_por_doc[id_progreso] = nuevo_progreso
                        st.rerun()
                    except Exception as e:
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
