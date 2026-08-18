# req_doc frontend

React + TypeScript + Vite + Ant Design frontend shell for the requirement governance workspace.

## Scripts

```bash
npm run dev
npm run test
npm run build
npm run preview
```

`npm run generate:api` is reserved for generating TypeScript API contract types from the FastAPI OpenAPI document into `src/api/generated/`.

## Architecture Boundary

- `src/api/` is the only frontend access layer for `/api`.
- `src/api/generated/` is generated from backend OpenAPI and should not be hand-edited.
- No `src/models/` directory is used. Backend DTOs and read models are the source for domain shape.
- `src/view-models/` contains UI projection types and mapper logic only.
- `src/workbenches/` renders ViewModels and does not call `fetch` directly.
