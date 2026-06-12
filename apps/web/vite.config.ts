import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// DEMO web client. Dev server on :5173; the demo API CORS-allows this origin.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
});
