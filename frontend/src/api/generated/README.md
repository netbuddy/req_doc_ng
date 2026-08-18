# Generated API Contracts

This directory is reserved for TypeScript types generated from the backend FastAPI OpenAPI document.

Do not hand-write domain objects here. Run:

```bash
npm run generate:api
```

The frontend consumes generated DTO/read-model types through `src/api/*` endpoint functions and maps them to UI ViewModels in `src/view-models/*`.

