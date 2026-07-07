(function () {
  "use strict";

  if (!window.Chart) return;
  const el = document.getElementById("mkt-data");
  if (!el) return;
  let mktData;
  try {
    mktData = JSON.parse(el.textContent);
  } catch (e) {
    console.error("Erro ao fazer parse dos dados de marketing:", e);
    return;
  }

  const monthly = mktData.monthly || [];
  const deptByMonth = mktData.deptByMonth || {};
  const atrasosData = mktData.atrasosData || [];
  const midia = mktData.midia || { meses: [], investimento: [], regioes: [], descontinuidades: [], aderencias: [] };

  const causaLabels = ["Sem causa registrada", "Aguardando definição interna", "Dependência de execução"];

  // ─── FILTER ───────────────────────────────────────────────────────────
  let activeFilter = "all";

  function filteredMonthly(){
    return activeFilter==="all" ? monthly : monthly.filter(m=>m.label===activeFilter);
  }

  function setFilter(val, btn){
    activeFilter=val;
    document.querySelectorAll(".filter-pill").forEach(b=>{
      b.classList.remove("active", "bg-navy-900", "text-white");
      b.classList.add("bg-gray-200", "text-navy");
    });
    btn.classList.add("active", "bg-navy-900", "text-white");
    btn.classList.remove("bg-gray-200", "text-navy");
    const d=filteredMonthly();
    
    const filtLabels = d.map(x => x.label);
    const periodLabel = val === "all" ? 
      (filtLabels[0] + " a " + filtLabels[filtLabels.length-1] + " — " + d.length + " meses") : 
      (val + " — 1 mês");
    document.getElementById("filter-period-label").textContent = periodLabel;
    
    const titleText = "Dashboard de Marketing — " + (val === "all" ? (filtLabels[0] + " a " + filtLabels[filtLabels.length-1]) : val);
    document.getElementById("header-title").textContent = titleText;
    
    renderSummary();
    renderAllCharts();
  }

  function renderSummary(){
    const d=filteredMonthly();
    if (d.length === 0) return;
    const last=d[d.length-1];
    const total=d.reduce((s,x)=>s+x.total,0);
    const conc=d.reduce((s,x)=>s+x.concluidas,0);
    const vol=d.reduce((s,x)=>s+x.volume,0);
    const mkt=d.reduce((s,x)=>s+x.mkt_orig,0);
    const pend=d.reduce((s,x)=>s+x.em_andamento+x.abertas,0);
    const pctConc=total?(conc/total*100).toFixed(1):"0";
    const pctMkt=total?(mkt/total*100).toFixed(1):"0";
    const razao=total?(vol/total).toFixed(1):"0";
    const tempoW=d.reduce((s,x)=>s+x.tempo_medio*x.concluidas,0)/(conc||1);
    const filtLabels=d.map(x=>x.label);
    const agg={};
    filtLabels.forEach(l=>Object.entries(deptByMonth[l]||{}).forEach(([k,v])=>{agg[k]=(agg[k]||0)+v;}));
    const top=Object.entries(agg).sort((a,b)=>b[1]-a[1])[0]||["—",0];
    const nAtr=atrasosData.filter(a=>filtLabels.includes(a.mes)).length;
    const pctAtr=total?(nAtr/total*100).toFixed(1):"0";

    document.getElementById("sum-left").innerHTML=`
      <div class="text-[10px] font-bold uppercase tracking-wider text-muted mb-3">📅 ${d.length===1?last.label:"Último mês — "+last.label}</div>
      <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div><h3 class="text-2xl font-black text-navy">${last.total}</h3><p class="text-xs text-muted">Total demandas</p></div>
        <div>
          <h3 class="text-2xl font-black text-brandgreen-600">${last.concluidas}</h3>
          <p class="text-xs text-muted">Concluídas</p>
          <div class="text-[10px] font-semibold text-brandgreen-600 mt-0.5">${last.pct_conc.toFixed(1).replace(".",",")}%</div>
        </div>
        <div><h3 class="text-2xl font-black text-blue-600">${last.em_andamento}</h3><p class="text-xs text-muted">Em andamento</p></div>
        <div><h3 class="text-2xl font-black text-red-500">${last.abertas}</h3><p class="text-xs text-muted">Abertas</p></div>
        <div><h3 class="text-2xl font-black text-amber-500">${last.volume}</h3><p class="text-xs text-muted">Volume</p></div>
        <div>
          <h3 class="text-2xl font-black text-brandgreen-600">${last.tempo_medio.toFixed(1).replace(".",",")} d</h3>
          <p class="text-xs text-muted">Tempo médio</p>
        </div>
        <div>
          <h3 class="text-2xl font-black text-purple-600">${last.pct_mkt.toFixed(1).replace(".",",")}%</h3>
          <p class="text-xs text-muted">Origem Mkt</p>
          <div class="text-[10px] text-muted mt-0.5">${last.mkt_orig} de ${last.total}</div>
        </div>
      </div>`;

    document.getElementById("sum-right").innerHTML=`
      <div class="text-[10px] font-bold uppercase tracking-wider text-muted mb-3">📊 ${d.length===1?"Resumo — "+last.label:"Acumulado — "+d[0].label+" a "+last.label}</div>
      <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div><h3 class="text-2xl font-black text-purple-600">${total}</h3><p class="text-xs text-muted">Total demandas</p></div>
        <div>
          <h3 class="text-2xl font-black text-brandgreen-600">${conc}</h3>
          <p class="text-xs text-muted">Concluídas</p>
          <div class="text-[10px] font-semibold text-brandgreen-600 mt-0.5">${pctConc.replace(".",",")}%</div>
        </div>
        <div><h3 class="text-2xl font-black text-red-500">${pend}</h3><p class="text-xs text-muted">Pendentes</p></div>
        <div>
          <h3 class="text-2xl font-black text-amber-500">${vol}</h3>
          <p class="text-xs text-muted">Volume</p>
          <div class="text-[10px] text-muted mt-0.5">x${razao.replace(".",",")} /dem.</div>
        </div>
        <div><h3 class="text-2xl font-black text-brandgreen-600">${tempoW.toFixed(1).replace(".",",")} d</h3><p class="text-xs text-muted">Tempo médio</p></div>
        <div>
          <h3 class="text-2xl font-black text-red-500">${nAtr}</h3>
          <p class="text-xs text-muted">Atrasos >5d</p>
          <div class="text-[10px] font-semibold text-red-500 mt-0.5">${pctAtr.replace(".",",")}%</div>
        </div>
        <div>
          <h3 class="text-2xl font-black text-purple-600">${pctMkt.replace(".",",")}%</h3>
          <p class="text-xs text-muted">Origem Mkt</p>
          <div class="text-[10px] text-muted mt-0.5">${mkt} de ${total}</div>
        </div>
        <div>
          <h3 class="text-base font-black text-navy truncate" title="${top[0]}">${top[0]}</h3>
          <p class="text-xs text-muted">Maior solicitante</p>
          <div class="text-[10px] text-muted mt-0.5">${top[1]} dem.</div>
        </div>
      </div>`;
  }

  // ─── CHART UTILS ──────────────────────────────────────────────────────
  const charts={};
  function mkChart(id,cfg){if(charts[id])charts[id].destroy();charts[id]=new Chart(document.getElementById(id),cfg);}

  const PILL={
    id:"pill",
    afterDatasetsDraw(chart){
      const {ctx}=chart;
      chart.data.datasets.forEach((ds,di)=>{
        if(ds.skipPill) return;
        const meta=chart.getDatasetMeta(di);
        if(meta.hidden) return;
        meta.data.forEach((el,i)=>{
          const val=ds.data[i];
          if(val===null||val===undefined||val===0) return;
          const {x,y}=el.tooltipPosition();
          const lbl=String(val);
          ctx.save();
          ctx.font="bold 10px Segoe UI,sans-serif";
          const w=ctx.measureText(lbl).width+10,h=16;
          ctx.fillStyle="rgba(255,255,255,0.93)";
          ctx.beginPath();ctx.roundRect(x-w/2,y-h-4,w,h,3);ctx.fill();
          ctx.fillStyle="#1a1d2e";ctx.textAlign="center";ctx.textBaseline="middle";
          ctx.fillText(lbl,x,y-h-4+h/2);
          ctx.restore();
        });
      });
    }
  };
  Chart.register(PILL);

  const LINE_LABEL_PLUGIN={
    id:"lineLabel",
    afterDatasetsDraw(chart){
      const {ctx}=chart;
      const meta=chart.getDatasetMeta(0);
      const ds=chart.data.datasets[0];
      meta.data.forEach((el,i)=>{
        const val=ds.data[i];
        const {x,y}=el.tooltipPosition();
        const lbl=val.toFixed(1).replace(".",",")+" d";
        ctx.save();
        ctx.font="bold 12px Segoe UI,sans-serif";
        const w=ctx.measureText(lbl).width+14,h=20;
        ctx.fillStyle="#fff";ctx.strokeStyle="#D85A30";ctx.lineWidth=1.5;
        ctx.beginPath();ctx.roundRect(x-w/2,y-h-6,w,h,4);ctx.fill();ctx.stroke();
        ctx.fillStyle="#D85A30";ctx.textAlign="center";ctx.textBaseline="middle";
        ctx.fillText(lbl,x,y-h-6+h/2);
        ctx.restore();
      });
    }
  };

  // ─── RENDER CHARTS ────────────────────────────────────────────────────
  function renderEntrega(){
    const d=filteredMonthly(),labels=d.map(m=>m.label);
    mkChart("chartEntrega",{type:"bar",data:{labels,datasets:[
      {label:"Concluídas",data:d.map(m=>m.concluidas),backgroundColor:"#1D9E75",borderRadius:5},
      {label:"Em andamento",data:d.map(m=>m.em_andamento),backgroundColor:"#378ADD",borderRadius:5},
      {label:"Abertas",data:d.map(m=>m.abertas),backgroundColor:"#D85A30",borderRadius:5},
      {label:"Total",data:d.map(m=>m.total),backgroundColor:"rgba(136,135,128,0.45)",borderRadius:5}
    ]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{position:"top"},pill:{}},
      scales:{x:{ticks:{font:{size:11}}},y:{beginAtZero:true}}}});
  }

  function renderVolume(){
    const d=filteredMonthly(),labels=d.map(m=>m.label);
    mkChart("chartVolume",{type:"bar",data:{labels,datasets:[
      {label:"Demandas",data:d.map(m=>m.total),backgroundColor:"#534AB7",borderRadius:5},
      {label:"Volume",data:d.map(m=>m.volume),backgroundColor:"#EF9F27",borderRadius:5}
    ]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{position:"top"},pill:{}},
      scales:{x:{ticks:{font:{size:11}}},y:{beginAtZero:true}}}});
  }

  function renderOrigem(){
    const d=filteredMonthly(),labels=d.map(m=>m.label);
    mkChart("chartOrigem",{type:"bar",data:{labels,datasets:[
      {label:"Marketing",data:d.map(m=>m.mkt_orig),backgroundColor:"#1D9E75",borderRadius:5},
      {label:"Solicitação",data:d.map(m=>m.sol_orig),backgroundColor:"#378ADD",borderRadius:5}
    ]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{position:"top"},pill:{}},
      scales:{x:{ticks:{font:{size:11}}},y:{beginAtZero:true}}}});
  }

  function renderDept(){
    const filtLabels=filteredMonthly().map(m=>m.label);
    const agg={};
    filtLabels.forEach(l=>Object.entries(deptByMonth[l]||{}).forEach(([k,v])=>{agg[k]=(agg[k]||0)+v;}));
    const keys=Object.keys(agg).sort((a,b)=>agg[b]-agg[a]);
    const vals=keys.map(k=>agg[k]);
    const colors=vals.map((_,i)=>i===0?"#1D9E75":i===1?"#378ADD":"rgba(83,74,183,0.65)");
    mkChart("chartDept",{type:"bar",data:{labels:keys,datasets:[{label:"Demandas",data:vals,backgroundColor:colors,borderRadius:5}]},
      options:{indexAxis:"y",responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},pill:{}},
        scales:{x:{beginAtZero:true},y:{ticks:{font:{size:11}}}}}});
  }

  // Define line labels plugin setup
  function renderTempo(){
    const d=filteredMonthly(),labels=d.map(m=>m.label),vals=d.map(m=>m.tempo_medio);
    if(charts["chartTempo"])charts["chartTempo"].destroy();
    charts["chartTempo"]=new Chart(document.getElementById("chartTempo"),{
      type:"line",plugins:[LINE_LABEL_PLUGIN],
      data:{labels,datasets:[
        {label:"Tempo médio (d)",data:vals,borderColor:"#D85A30",backgroundColor:"rgba(216,90,48,0.15)",fill:true,tension:0.35,pointBackgroundColor:"#D85A30",pointRadius:6,pointHoverRadius:8},
        {label:"Limite 5d",data:labels.map(()=>5),borderColor:"#e05252",borderWidth:2,borderDash:[6,3],pointRadius:0,fill:false,skipPill:true}
      ]},
      options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{position:"top"},lineLabel:{}},
        scales:{y:{beginAtZero:true,max:8,ticks:{callback:v=>v+" d"}},x:{ticks:{font:{size:11}}}}}
    });
  }

  function renderCausas(){
    const d = filteredMonthly();
    const filtLabels = d.map(x => x.label);
    const rows = atrasosData.filter(a => filtLabels.includes(a.mes));
    const causasCount = {};
    causaLabels.forEach(lbl => causasCount[lbl] = 0);
    rows.forEach(a => {
      const c = a.causa || "Sem causa registrada";
      causasCount[c] = (causasCount[c] || 0) + 1;
    });
    const causaVals = causaLabels.map(lbl => causasCount[lbl] || 0);

    mkChart("chartCausas",{type:"bar",data:{labels:causaLabels,datasets:[{label:"Ocorrências",data:causaVals,backgroundColor:"#D85A30",borderRadius:5}]},
      options:{indexAxis:"y",responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},pill:{}},
        scales:{x:{beginAtZero:true,ticks:{stepSize:1}},y:{ticks:{font:{size:11}}}}}});
  }

  function renderMidia(){
    mkChart("chartMidiaIndicadores",{type:"bar",data:{labels:midia.meses,datasets:[
      {label:"Regiões Ativas",data:midia.regioes,backgroundColor:"#378ADD",borderRadius:5},
      {label:"Descontinuidades",data:midia.descontinuidades,backgroundColor:"#D85A30",borderRadius:5},
      {label:"Aderências",data:midia.aderencias,backgroundColor:"#1D9E75",borderRadius:5}
    ]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{position:"top"},pill:{}},
      scales:{x:{ticks:{font:{size:11}}},y:{beginAtZero:true}}}});
    mkChart("chartMidiaInvest",{type:"line",data:{labels:midia.meses,datasets:[
      {label:"Investimento BD (R$)",data:midia.investimento,borderColor:"#534AB7",backgroundColor:"rgba(83,74,183,0.15)",fill:true,tension:0.35,pointBackgroundColor:"#534AB7",pointRadius:6}
    ]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{position:"top"},pill:{}},
      scales:{y:{ticks:{callback:v=>"R$ "+v.toLocaleString("pt-BR")}},x:{ticks:{font:{size:11}}}}}});
  }

  function buildAtrasosTable(){
    const filtLabels=filteredMonthly().map(m=>m.label);
    const rows=atrasosData.filter(a=>filtLabels.includes(a.mes));
    const tbody=document.getElementById("atrasos-tbody");
    if (tbody) {
      tbody.innerHTML=rows.map(a=>`<tr>
        <td class="p-2 border-b font-medium text-navy">${a.nome}</td><td class="p-2 border-b">${a.mes}</td>
        <td class="p-2 border-b"><span class="tag-atraso bg-red-50 text-red-700 px-2 py-0.5 rounded-full font-bold text-[10px]">${a.dias}d</span></td>
        <td class="p-2 border-b"><span class="tag-causa bg-blue-50 text-blue-700 px-2 py-0.5 rounded-full font-medium text-[10px]">${a.causa}</span></td>
      </tr>`).join("");
    }
  }

  function updateTempoInsights() {
    const d = filteredMonthly();
    if (d.length === 0) return;
    
    const conc = d.reduce((s,x)=>s+x.concluidas, 0);
    const tempoW = d.reduce((s,x)=>s+x.tempo_medio*x.concluidas,0)/(conc||1);
    document.getElementById("tempo-media-acumulado").textContent = tempoW.toFixed(1).replace(".",",") + " d";
    
    const filtLabels = d.map(x => x.label);
    const total = d.reduce((s,x)=>s+x.total, 0);
    const rows = atrasosData.filter(a => filtLabels.includes(a.mes));
    const nAtr = rows.length;
    const pctAtr = total ? ((nAtr / total) * 100).toFixed(1) : "0";
    document.getElementById("tempo-atrasos-qtd").textContent = nAtr;
    document.getElementById("tempo-atrasos-pct").textContent = pctAtr.replace(".",",") + "% do total";
    
    let maxAtr = 0;
    let maxAtrNome = "—";
    rows.forEach(a => {
      if (a.dias > maxAtr) {
        maxAtr = a.dias;
        maxAtrNome = a.nome + " — " + a.mes;
      }
    });
    document.getElementById("tempo-maior-atraso").textContent = maxAtr + " d";
    document.getElementById("tempo-maior-atraso-nome").textContent = maxAtrNome;
    
    let melhorMedia = 999.0;
    let melhorMediaMes = "—";
    d.forEach(m => {
      if (m.concluidas > 0 && m.tempo_medio < melhorMedia) {
        melhorMedia = m.tempo_medio;
        melhorMediaMes = m.label;
      }
    });
    document.getElementById("tempo-melhor-media").textContent = melhorMedia === 999.0 ? "—" : melhorMedia.toFixed(1).replace(".",",") + " d";
    document.getElementById("tempo-melhor-media-mes").textContent = melhorMediaMes + " — tendência positiva";
    
    const causasCount = {};
    rows.forEach(a => {
      const c = a.causa || "Sem causa registrada";
      causasCount[c] = (causasCount[c] || 0) + 1;
    });
    const topCausa = Object.entries(causasCount).sort((a,b)=>b[1]-a[1])[0];
    if (topCausa) {
      const pctCausa = nAtr ? ((topCausa[1] / nAtr) * 100).toFixed(1) : "0";
      document.getElementById("tempo-causa-principal").textContent = topCausa[0];
      document.getElementById("tempo-causa-principal-detalhes").textContent = topCausa[1] + " de " + nAtr + " atrasos (" + pctCausa.replace(".",",") + "%)";
    } else {
      document.getElementById("tempo-causa-principal").textContent = "—";
      document.getElementById("tempo-causa-principal-detalhes").textContent = "Nenhum atraso registrado";
    }
  }

  function updateMidiaInsights() {
    const activeMonths = filteredMonthly().map(m => m.label.split("/")[0].toLowerCase());
    
    let investTotal = 0;
    let totalDesc = 0;
    let totalAder = 0;
    let lastRegioes = 0;
    let lastMonthName = "—";
    
    let firstMonthInvest = 0;
    let lastMonthInvest = 0;
    let countMonths = 0;
    
    midia.meses.forEach((m, idx) => {
      if (activeMonths.includes(m.toLowerCase())) {
        investTotal += midia.investimento[idx];
        totalDesc += midia.descontinuidades[idx];
        totalAder += midia.aderencias[idx];
        lastRegioes = midia.regioes[idx];
        lastMonthName = m + "/26";
        
        if (countMonths === 0) {
          firstMonthInvest = midia.investimento[idx];
        }
        lastMonthInvest = midia.investimento[idx];
        countMonths++;
      }
    });
    
    document.getElementById("midia-investimento-total").textContent = "R$ " + investTotal.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    document.getElementById("midia-regioes-var").textContent = lastRegioes;
    document.getElementById("midia-regioes-nota").textContent = lastMonthName;
    document.getElementById("midia-descontinuidades").textContent = totalDesc;
    document.getElementById("midia-aderencias").textContent = totalAder;
    
    if (countMonths > 1 && firstMonthInvest > 0) {
      const varPct = ((lastMonthInvest - firstMonthInvest) / firstMonthInvest) * 100;
      const sign = varPct > 0 ? "+" : "";
      document.getElementById("midia-invest-var").textContent = sign + varPct.toFixed(1).replace(".",",") + "%";
      document.getElementById("midia-invest-var-detalhe").textContent = "De R$ " + firstMonthInvest.toLocaleString("pt-BR") + " para R$ " + lastMonthInvest.toLocaleString("pt-BR");
    } else {
      document.getElementById("midia-invest-var").textContent = "—";
      document.getElementById("midia-invest-var-detalhe").textContent = "Selecione o acumulado para ver variação";
    }
  }

  function renderAllCharts(){
    renderEntrega();
    const activeTabBtn = document.querySelector(".tab-btn.active");
    const id = activeTabBtn ? activeTabBtn.outerHTML.match(/switchTab\('([^']+)'/)[1] : "entrega";
    
    if(id==="volume")renderVolume();
    else if(id==="origem")renderOrigem();
    else if(id==="dept")renderDept();
    else if(id==="tempo"){renderTempo();buildAtrasosTable();renderCausas();updateTempoInsights();}
    else if(id==="midia"){renderMidia();updateMidiaInsights();}
  }

  // ─── TABS ─────────────────────────────────────────────────────────────
  function switchTab(name,btn){
    document.querySelectorAll(".tab-panel").forEach(p=>p.classList.add("hidden"));
    document.querySelectorAll(".tab-panel").forEach(p=>p.classList.remove("active"));
    
    document.getElementById("tab-"+name).classList.remove("hidden");
    document.getElementById("tab-"+name).classList.add("active");
    
    document.querySelectorAll(".tab-btn").forEach(b=>{
      b.classList.remove("active", "bg-brandgreen-600", "text-white", "shadow-soft");
      b.classList.add("bg-gray-200", "text-navy");
    });
    btn.classList.add("active", "bg-brandgreen-600", "text-white", "shadow-soft");
    btn.classList.remove("bg-gray-200", "text-navy");
    
    if(name==="entrega")renderEntrega();
    else if(name==="volume")renderVolume();
    else if(name==="origem")renderOrigem();
    else if(name==="dept")renderDept();
    else if(name==="tempo"){renderTempo();buildAtrasosTable();renderCausas();updateTempoInsights();}
    else if(name==="midia"){renderMidia();updateMidiaInsights();}
  }

  // Expose callbacks globally so they can be triggered from onclick attributes
  window.setFilter = setFilter;
  window.switchTab = switchTab;

  // ─── INIT ─────────────────────────────────────────────────────────────
  const d = filteredMonthly();
  if (d.length > 0) {
    const filtLabels = d.map(x => x.label);
    const periodLabelEl = document.getElementById("filter-period-label");
    if (periodLabelEl) {
      periodLabelEl.textContent = filtLabels[0] + " a " + filtLabels[filtLabels.length-1] + " — " + d.length + " meses";
    }
    renderSummary();
    renderEntrega();
  }
})();
