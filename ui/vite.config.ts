import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

/**
 * Der Dev-Server muss im LAN erreichbar sein, damit das iPad ihn öffnen kann.
 *
 * `getUserMedia` verlangt in Safari HTTPS (oder localhost). Für die Arbeit am
 * iPad also entweder Tailscale (bringt TLS mit) oder ein lokales Zertifikat
 * per mkcert — siehe docs/phase2-abnahme.md.
 */
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
  },
  build: {
    target: 'es2022',
    sourcemap: true,
  },
});
