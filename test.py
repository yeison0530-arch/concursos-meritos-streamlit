from app import descargar_documento_cache, safe_name
import fitz

ruta = f"{safe_name('Concurso Procuraduría')}/9._Ley_1952_de_2019_Codigo_General_Disciplinario.pdf"
print("Ruta:", ruta)
try:
    res = descargar_documento_cache(ruta)
    doc = fitz.open(stream=res, filetype='pdf')
    txt = ''.join([p.get_text() for p in doc])
    print('EXTRACTED CHARS:', len(txt))
except Exception as e:
    print('ERROR:', e)
