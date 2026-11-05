# Contribuir

Las contribuciones deben mantener el carácter educativo del proyecto y no
convertir las decisiones contables en acciones automáticas.

## Antes de proponer cambios

1. Revisa el alcance y las reglas de privacidad en [README.md](./README.md).
2. No agregues claves, contraseñas, tokens, datos personales ni documentos
   reales. Usa datos sintéticos.
3. Para cambios grandes, abre primero una propuesta o issue con el problema,
   alcance y criterios de aceptación.

## Flujo de trabajo

1. Crea una rama descriptiva desde la rama principal.
2. Mantén los cambios acotados y agrega pruebas para el comportamiento nuevo.
3. Ejecuta las comprobaciones aplicables:

   ```bash
   cd azure/email-screening
   python -m unittest discover -s tests -v
   python prepare_dataset.py --output /tmp/rioclaro-synthetic-dataset
   cd ../..
   python -m compileall -q azure/email-screening custom_addons scripts
   docker compose config -q
   ```

4. Describe en el pull request el problema, la solución, las pruebas ejecutadas
   y cualquier cambio visible o de configuración. No adjuntes capturas con
   información personal o de infraestructura.

## Convenciones

- Conserva los patrones de Odoo y Python ya usados en cada componente.
- Mantén los servicios de IA opcionales cuando sea posible; las pruebas no deben
  requerir credenciales ni llamar a servicios reales.
- Los resultados de IA son sugerencias sujetas a revisión humana.
- Actualiza la documentación del componente cuando cambien su configuración o
  comportamiento.
