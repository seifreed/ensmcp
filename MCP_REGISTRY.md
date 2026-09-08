# Registro de autorización MCP

El mantenedor autoriza la publicación de los metadatos oficiales de este
servidor en el MCP Registry bajo el nombre `io.github.seifreed/ensmcp`.

## Metadatos autorizados

- Fuente canónica: `server.json`.
- Repositorio: `https://github.com/seifreed/ensmcp`.
- Paquete: `ensmcp` en PyPI, ejecutado mediante `stdio`.
- Publicación: automática tras publicar el paquete de una release firmada.
- Identidad: GitHub OIDC del repositorio, sin token persistente.

El transporte Streamable HTTP es una opción local o detrás de un proxy propio;
no se anuncia como servidor remoto porque el proyecto no opera una URL pública.

El registro oficial mostraba la versión `0.1.1` con estado `active` al incorporar
este documento. Puede comprobarse en la
[API del MCP Registry](https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.seifreed%2Fensmcp).

Responsable: Marc Rivero López (`@seifreed`)

Registro incorporado al repositorio: 2026-09-08
