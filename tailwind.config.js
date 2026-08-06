/** @type {import('tailwindcss').Config} */
// Tailwind v3.4 (Seção 0.4 / C3): build via CLI, purge apontando para os
// templates Jinja. Sem CSS customizado à mão — apenas utilitários.
// Tokens da marca portados do protótipo aprovado (Manual de Identidade Visual).
module.exports = {
  // Inclui os .js de /static: classes aplicadas via JS (ex.: toggle "nota interna",
  // ghost do Sortable) precisam ser vistas pelo purge.
  // Inclui .py porque metadados de UI (classes de status/prioridade em
  // app/templating.py) são aplicados dinamicamente e precisam ser vistos pelo purge.
  content: ["./app/templates/**/*.html", "./app/static/js/**/*.js", "./app/**/*.py"],
  theme: {
    extend: {
      colors: {
        navy: { 900: "#0E2747", 800: "#14315A", 700: "#1C3A63", DEFAULT: "#2E466F", 500: "#3D5A8A", 100: "#E7ECF3", 50: "#F3F6FB" },
        brandgreen: { DEFAULT: "#ACC76B", 600: "#7FA53D", 700: "#5F8A2E", 100: "#EEF4E0" },
        ink: "#1E293B", muted: "#64748B", faint: "#94A3B8",
        surface: "#F4F6F9", surface2: "#EAEEF4", line: "#E2E8F0",
        st_novo: "#2563EB", st_atend: "#6366F1", st_aguard: "#F59E0B", st_resolv: "#16A34A",
        st_terceiros: "#EA580C", st_projetos: "#7C3AED", st_resp_cliente: "#0D9488",
        // Badges de status / SLA (Seção 5.1 / Fase 4)
        sla: { ok: "#16a34a", warn: "#f59e0b", danger: "#dc2626" },
        pr_baixa: "#64748B", pr_media: "#2563EB", pr_alta: "#F59E0B", pr_urgente: "#DC2626",
      },
      fontFamily: {
        display: ["Archivo", "ui-sans-serif", "system-ui", "sans-serif"],
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
      },
      boxShadow: {
        card: "0 1px 2px rgba(16,33,71,.04), 0 4px 16px rgba(16,33,71,.06)",
        soft: "0 1px 3px rgba(16,33,71,.08)",
      },
    },
  },
  plugins: [],
};
