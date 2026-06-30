/** @type {import('tailwindcss').Config} */
// Tailwind v3.4 (Seção 0.4 / C3): build via CLI, purge apontando para os
// templates Jinja. Sem CSS customizado à mão — apenas utilitários.
module.exports = {
  content: ["./app/templates/**/*.html"],
  theme: {
    extend: {
      colors: {
        // Badges de status / SLA (Seção 5.1 / Fase 4)
        sla: {
          ok: "#16a34a",      // verde
          warn: "#f59e0b",    // amarelo (<25%)
          danger: "#dc2626",  // vermelho (<10% ou vencido)
        },
      },
    },
  },
  plugins: [],
};
