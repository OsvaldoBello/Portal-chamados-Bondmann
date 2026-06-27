/** Tailwind CSS 3.4 — Portal de Chamados Bondmann Química (tokens da marca) */
module.exports = {
  content: ['./*.html', './prototipos/*.html'],
  theme: {
    extend: {
      colors: {
        navy: { 900:'#0E2747', 800:'#14315A', 700:'#1C3A63', DEFAULT:'#2E466F', 500:'#3D5A8A', 100:'#E7ECF3' },
        brandgreen: { DEFAULT:'#ACC76B', 600:'#7FA53D', 700:'#5F8A2E', 100:'#EEF4E0' },
        ink:'#1E293B', muted:'#64748B', faint:'#94A3B8',
        surface:'#F4F6F9', surface2:'#EAEEF4', line:'#E2E8F0',
        st_novo:'#2563EB', st_atend:'#6366F1', st_aguard:'#F59E0B', st_resolv:'#16A34A',
        sla_ok:'#16A34A', sla_warn:'#F59E0B', sla_danger:'#DC2626',
        pr_baixa:'#64748B', pr_media:'#2563EB', pr_alta:'#F59E0B', pr_urgente:'#DC2626',
        ac_clean:'#1FB98C', ac_auto:'#41B6E6', ac_industry:'#5560B0', ac_fluid:'#E63E62', ac_max:'#F0934E', ac_service:'#1B8A8F',
      },
      fontFamily: {
        display: ['Archivo','ui-sans-serif','system-ui','sans-serif'],
        sans: ['Inter','ui-sans-serif','system-ui','sans-serif'],
      },
      boxShadow: {
        card:'0 1px 2px rgba(16,33,71,.04), 0 4px 16px rgba(16,33,71,.06)',
        soft:'0 1px 3px rgba(16,33,71,.08)',
      },
      keyframes: {
        pulseDanger: { '0%,100%':{ boxShadow:'0 0 0 0 rgba(220,38,38,.45)' }, '50%':{ boxShadow:'0 0 0 6px rgba(220,38,38,0)' } },
      },
      animation: { pulseDanger:'pulseDanger 1.4s ease-in-out infinite' },
    },
  },
  plugins: [],
};
