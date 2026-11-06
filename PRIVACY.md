# Política de privacidad — PopSay

Última actualización: 5 de noviembre de 2026

**Qué datos se procesan.** Únicamente el texto que vos seleccionás y pedís pronunciar.

**Dónde se procesan.**

- Con el motor *Voces del sistema*, el texto se pasa a la API de voz del navegador (`chrome.tts`). Algunas voces "online" del navegador o del sistema operativo pueden enviar el texto a su proveedor (Google o Microsoft) según sus propias políticas.
- Para la transcripción IPA sin backend, cada palabra seleccionada (hasta 12) se consulta en la Free Dictionary API (`api.dictionaryapi.dev`).
- Con el motor *Neuronal*, el texto se envía al servidor que vos configures en Ajustes, que a su vez lo envía al proveedor de voz que ese servidor tenga configurado (Azure, OpenAI o ElevenLabs).

**Qué se guarda.** Los ajustes de la extensión se guardan en `chrome.storage.sync` de tu navegador. El backend puede guardar en caché el audio generado (clave: hash del texto) por hasta 30 días para no volver a pagarlo. No se guardan historiales asociados a personas, no hay analítica ni publicidad, y no se vende ni comparte información.

**Contacto.** Abrí un issue en el repositorio del proyecto.
