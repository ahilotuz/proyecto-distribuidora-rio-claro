# Lista antes de publicar

El repositorio contiene un prototipo académico y documentación de despliegues
reales. Completa estas verificaciones con las personas responsables antes de
abrirlo al público:

- [ ] Elegir una licencia raíz y confirmar los derechos sobre código,
      documentación, capturas y materiales de terceros.
- [ ] Revisar **todo el historial Git**, no solo el árbol actual, en busca de
      claves, tokens, contraseñas, direcciones personales, IDs de suscripción y
      datos de clientes. Si hubo secretos, revocarlos/rotarlos y acordar una
      purga del historial; borrarlos en un commit nuevo no los invalida.
- [ ] Revisar visualmente todas las capturas para confirmar que no quedan datos
      personales, identificadores de recursos o destinos de infraestructura
      activa.
- [ ] Confirmar que `.env`, `local.settings.json` y las configuraciones Odoo de
      desarrollo/producción no estén versionados.
- [ ] Activar secret scanning y push protection si están disponibles; habilitar
      reportes privados de vulnerabilidades.
- [ ] Proteger la rama principal y requerir que pase el workflow de pruebas.
- [ ] Verificar que las instrucciones de despliegue no expongan servicios,
      credenciales ni datos reales.
- [ ] Añadir mantenedores/contacto de soporte y canales de contribución válidos.

La selección de licencia y la revisión del historial son decisiones de los
autores; este proyecto no las realiza automáticamente.
