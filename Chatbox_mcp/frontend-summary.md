# Frontend Summary

## What is present
- The frontend is a React app under `frontend/src`.
- `frontend/src/App.jsx` is the top-level component that:
  - checks for a stored JWT token in `localStorage` on startup,
  - validates it by calling `GET /api/auth/me`,
  - stores authenticated user data in React state,
  - renders `LoginPage` when not authenticated,
  - renders `ChatBox` when authenticated.
- User login state is persisted in browser `localStorage` under the key `token`.

## Onboarding / user flow
- There is no visible self-registration form in `App.jsx` itself; the current app flow is login-first.
- On app mount, if a token exists, the frontend sends it to the backend to confirm it is still valid.
- If the token is invalid, the frontend removes it and sends the user to login again.

## Token handling
- The frontend stores only the JWT from the backend in `localStorage`.
- The frontend does not store or manage Simpro or MYOB credentials directly.
- All backend integration credentials are kept on the backend side.

## What is not present / important note
- `mcp-client` is not used by the React frontend for onboarding or auth state.
- The frontend does not appear to have any built-in UI for entering or updating Simpro/MyOB credentials.
- The frontend simply passes the auth JWT to backend API calls.
