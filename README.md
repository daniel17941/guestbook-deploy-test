# guestbook-deploy-test

A minimal full-stack test of an automated deploy pipeline.

- Frontend: Next.js page with a message form.
- Backend: `/api/messages` route (GET lists, POST saves).
- Storage: Vercel Blob.
- Pipeline: push to `main` → GitHub Actions → `vercel deploy --prebuilt --prod`.

This repository is a throwaway test. It verifies push-to-deploy for a site with a backend.
