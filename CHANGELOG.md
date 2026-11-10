# Changelog

Los cambios de cada versión de PopSay. Las versiones siguen [SemVer](https://semver.org/lang/es/).

## 1.6.0 — 2026-11-10

### Nuevo
- **El IPA coincide con la voz.** Cuando una palabra tiene más de una pronunciación (*read*, *live*, *record*, *the*), la burbuja muestra la que el motor dice en esa oración. Antes mostraba siempre la primera del diccionario: "I will read it" aparecía como /rɛd/.
- Los números y las siglas ahora tienen transcripción ("2026", "$3.50", "9:30"), tomada de los mismos fonemas que usa la voz. Antes quedaban en blanco.
- El diccionario IPA incluye las pronunciaciones alternativas de CMUdict (8.052 palabras con variantes).
- Lo mismo en la extensión de Chrome/Edge, con casos de prueba compartidos entre Python y JavaScript.

### Proyecto
- Licencia MIT.
- README en inglés (`README.md`) y en español (`README.es.md`).
- Los tests y el linter (ruff) corren en GitHub Actions en cada push.

### Arreglos
- Las sílabas del IPA se cortan bien cuando la transcripción viene de espeak-ng o tiene varias palabras.

## 1.5.1 — 2026-11-08

### Arreglos
- **Silencios largos en comas y puntos.** La app mide cuánto tarda la compu en generar la voz y dimensiona cada fragmento para que esté listo antes de que termine el anterior. Los cortes caen en límites de frase.
- El mismo arreglo en la extensión.

## 1.5.0 — 2026-11-06

Primera versión publicada.

- Burbuja que pronuncia el texto seleccionado en cualquier programa de Windows, sin internet.
- Voz neuronal Kokoro-82M con 8 voces (4 de EE. UU. y 4 británicas) y velocidad regulable.
- IPA con resaltado de la palabra y la sílaba que suena.
- La burbuja se arrastra desde la cabecera y se agranda desde la esquina; el tamaño se recuerda.
- Síntesis por fragmentos y caché en disco para empezar a sonar enseguida.
- Extensión de Chrome/Edge (Manifest V3) con el mismo motor en WebAssembly.
- Límite de 2000 caracteres por selección.
