from app import descargar_documento_cache, safe_name
import fitz

ruta = f"{safe_name('Concurso Procuraduría')}/9._Ley_1952_de_2019_Codigo_General_Disciplinario.pdf"
res = descargar_documento_cache(ruta)
doc = fitz.open(stream=res, filetype='pdf')
txt = ''.join([p.get_text() for p in doc])

# Simulate progress 0% and 8.25%
largo = len(txt)
chunk1 = txt[0:5400]
chunk2 = txt[int(0.0825*largo) : int(0.0825*largo)+5400]

print("--- CHUNK 1 (0%) ---")
print(chunk1[:500])
print("\n--- CHUNK 2 (8.25%) ---")
print(chunk2[:500])
