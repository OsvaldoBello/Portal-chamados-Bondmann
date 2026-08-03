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
  const operadorByMonth = mktData.operadorByMonth || {};
  const resolvidosSemOperador = mktData.resolvidosSemOperadorByMonth || {};
  const atrasosData = mktData.atrasosData || [];
  const midia = mktData.midia || { meses: [], investimento: [], regioes: [], descontinuidades: [], aderencias: [] };

  const causaLabels = ["Sem causa registrada", "Aguardando definição interna", "Dependência de execução"];

  // ─── FILTER ───────────────────────────────────────────────────────────
  let activeFilter = "all";

  function filteredMonthly(){
    return activeFilter==="all" ? monthly : monthly.filter(m=>m.label===activeFilter);
  }

  // `tempo_medio` vem `null` do backend nos meses sem NENHUMA demanda concluída
  // (média de zero itens não é "0 dias", é "sem dado" — ver app/repositories/
  // admin.py::mkt_dashboard_data). Média ponderada por `concluidas`, ignorando
  // os meses sem dado tanto no numerador quanto no denominador; `null` quando
  // nenhum mês do período tem tempo médio conhecido.
  function mediaTempoPonderada(lista){
    let peso=0, soma=0;
    lista.forEach(m=>{
      if(m.tempo_medio!=null){ soma+=m.tempo_medio*m.concluidas; peso+=m.concluidas; }
    });
    return peso ? soma/peso : null;
  }

  function fmtDias(valor){
    return valor!=null ? valor.toFixed(1).replace(".",",")+" d" : "—";
  }

  // Rótulo do mês corrente no MESMO formato do backend ("AGO/26" — ver
  // `AdminRepo._mes_label`). Serve pra reconhecer o mês ainda em andamento na
  // série e não compará-lo com meses fechados.
  const MESES_LABEL=["JAN","FEV","MAR","ABR","MAI","JUN","JUL","AGO","SET","OUT","NOV","DEZ"];
  function labelMesCorrente(){
    const hoje=new Date();
    return MESES_LABEL[hoje.getMonth()]+"/"+String(hoje.getFullYear()%100).padStart(2,"0");
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

    // Exportação dinâmica: o link sempre exporta o período que está selecionado
    // na tela agora (ver app/services/export_marketing.py).
    const btnExportar = document.getElementById("btn-exportar");
    if (btnExportar) {
      btnExportar.href = "/admin/marketing/export?periodo=" + encodeURIComponent(val);
    }

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
    const tempoW=mediaTempoPonderada(d);
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
          <h3 class="text-2xl font-black text-brandgreen-600">${fmtDias(last.tempo_medio)}</h3>
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
        <div><h3 class="text-2xl font-black text-brandgreen-600">${fmtDias(tempoW)}</h3><p class="text-xs text-muted">Tempo médio</p></div>
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
          const lbl=ds.pillFormat ? ds.pillFormat(val) : String(val);
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
        if(val===null||val===undefined) return;  // mês sem tempo médio (sem concluída) — sem pill
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

  // Indicadores da aba "Volume Produzido" (pedido do usuário 2026-08-03).
  // Tudo derivado de `monthly` — nada hardcoded — e respeitando o filtro de
  // período. O crescimento é a única métrica que olha FORA do filtro: com um
  // mês só selecionado, o mês anterior não está em `filteredMonthly()`, então
  // o comparativo sai da série completa (`monthly`) — selecionar MAR continua
  // mostrando "MAR vs FEV", e não "sem comparativo".
  function updateVolumeInsights(){
    const d=filteredMonthly();
    const setTexto=(id,txt)=>{const el=document.getElementById(id);if(el)el.textContent=txt;};
    if(d.length===0){
      ["volume-total","volume-razao","volume-crescimento","volume-razao-mes"].forEach(id=>setTexto(id,"—"));
      ["volume-total-nota","volume-crescimento-nota","volume-razao-mes-nota"].forEach(id=>setTexto(id,"—"));
      return;
    }
    // Mês de referência dos cards "Crescimento" e "Razão do mês".
    //
    // No acumulado NÃO pode ser o último mês da série quando ele é o mês
    // CORRENTE: comparar 3 dias de agosto contra julho inteiro dava
    // "-99,0% (314 → 3)" — aritmeticamente certo, mas lido como colapso do
    // time. Nesse caso a referência recua pro último mês FECHADO (pedido do
    // usuário 2026-08-03). Quando o filtro aponta um mês específico, a
    // escolha do usuário manda — inclusive se for o mês em curso.
    const emCurso=labelMesCorrente();
    let ref=d[d.length-1];
    let correnteIgnorado=false;
    if(activeFilter==="all" && d.length>1 && ref.label===emCurso){
      ref=d[d.length-2];
      correnteIgnorado=true;
    }
    const vol=d.reduce((s,x)=>s+x.volume,0);
    const total=d.reduce((s,x)=>s+x.total,0);

    setTexto("volume-total",vol);
    // Quebra por mês. Períodos longos estouram a largura do card: a lista fica
    // com `truncate` no HTML e o texto completo vai no `title` (tooltip).
    const quebra=d.map(m=>m.label+": "+m.volume).join(" · ");
    setTexto("volume-total-nota",quebra);
    const notaEl=document.getElementById("volume-total-nota");
    if(notaEl) notaEl.title=quebra;

    setTexto("volume-razao",total?"×"+(vol/total).toFixed(1).replace(".",","):"—");
    setTexto("volume-razao-nota",d.length===1?"Média do mês":"Média acumulada");

    // Crescimento do mês de referência vs o mês imediatamente anterior da
    // série. `null` de volume não existe aqui (a view/baseline sempre devolve
    // número), mas mês anterior com volume 0 daria divisão por zero.
    const idxRef=monthly.findIndex(m=>m.label===ref.label);
    const anterior=idxRef>0?monthly[idxRef-1]:null;
    const cresEl=document.getElementById("volume-crescimento");
    if(anterior&&anterior.volume>0){
      const pct=(ref.volume-anterior.volume)/anterior.volume*100;
      setTexto("volume-crescimento",(pct>0?"+":"")+pct.toFixed(1).replace(".",",")+"%");
      setTexto("volume-crescimento-rotulo","Crescimento "+ref.label+" vs "+anterior.label);
      setTexto("volume-crescimento-nota",anterior.volume+" → "+ref.volume
        +(correnteIgnorado?" · "+emCurso+" em curso":""));
      if(cresEl){
        cresEl.classList.toggle("text-red-600",pct<0);
        cresEl.classList.toggle("text-brandgreen-700",pct>=0);
      }
    }else{
      setTexto("volume-crescimento","—");
      setTexto("volume-crescimento-rotulo","Crescimento mês a mês");
      setTexto("volume-crescimento-nota",anterior?anterior.label+" sem volume":"Sem mês anterior na série");
      if(cresEl){
        cresEl.classList.remove("text-red-600");
        cresEl.classList.add("text-brandgreen-700");
      }
    }

    // Mesma referência do crescimento: os dois cards falam do "último mês", e
    // apontar para meses diferentes lado a lado seria pior que o problema
    // original. O rótulo sempre nomeia o mês, então não há ambiguidade.
    setTexto("volume-razao-mes",ref.total?"×"+(ref.volume/ref.total).toFixed(1).replace(".",","):"—");
    setTexto("volume-razao-mes-rotulo","Razão "+ref.label);
    setTexto("volume-razao-mes-nota",ref.volume+" vol · "+ref.total+" demandas");
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
    const aviso=document.getElementById("dept-aviso");
    if(aviso){
      // Marketing aparece com DOIS números no dashboard e isso confunde — foi
      // o que gerou o report de "não bate". Aqui os dois ficam lado a lado
      // (decisão do usuário 2026-08-03): esta aba conta o setor PARA QUEM a
      // demanda foi feita; a aba 3 conta de quem partiu a INICIATIVA. São
      // colunas diferentes do controle do Marketing e divergem de verdade.
      const destino=agg["Marketing"]||0;
      const iniciativa=filteredMonthly().reduce((s,m)=>s+m.mkt_orig,0);
      aviso.textContent="Marketing: "+destino+" como setor de destino da demanda (este gráfico)"
        +" · "+iniciativa+" como origem da iniciativa (aba \"3. Origem da Demanda\")."
        +(destino!==iniciativa
          ? " A diferença são peças feitas para outros setores por iniciativa do próprio Marketing."
          : "");
    }
    mkChart("chartDept",{type:"bar",data:{labels:keys,datasets:[{label:"Demandas",data:vals,backgroundColor:colors,borderRadius:5}]},
      options:{indexAxis:"y",responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},pill:{}},
        scales:{x:{beginAtZero:true},y:{ticks:{font:{size:11}}}}}});
  }

  // Define line labels plugin setup
  function renderTempo(){
    const d=filteredMonthly(),labels=d.map(m=>m.label),vals=d.map(m=>m.tempo_medio);
    // Teto do eixo DINÂMICO: um mês com tempo médio bem acima do normal (ex.
    // 184d — chamado antigo/histórico só resolvido meses depois) não pode
    // ficar preso num teto fixo de 8d. O Chart.js não "recorta" o ponto na
    // borda do eixo — ele EXTRAPOLA a linha matematicamente bem pra fora da
    // área visível (y pode virar -9000px), e o pedaço que cruza de volta pro
    // canvas aparece como um pico/corte quebrado (era o "ainda está cortado").
    // Teto = sempre cobre o maior valor do período + folga de 15%, com piso
    // de 8d (mantém a régua "Limite 5d" com folga visual nos meses normais).
    const maiorValor = vals.reduce((m,v)=> v!=null && v>m ? v : m, 0);
    const eixoMax = Math.max(8, Math.ceil(maiorValor*1.15));
    if(charts["chartTempo"])charts["chartTempo"].destroy();
    charts["chartTempo"]=new Chart(document.getElementById("chartTempo"),{
      type:"line",plugins:[LINE_LABEL_PLUGIN],
      data:{labels,datasets:[
        {label:"Tempo médio (d)",data:vals,borderColor:"#D85A30",backgroundColor:"rgba(216,90,48,0.15)",fill:true,tension:0.35,pointBackgroundColor:"#D85A30",pointRadius:6,pointHoverRadius:8},
        {label:"Limite 5d",data:labels.map(()=>5),borderColor:"#e05252",borderWidth:2,borderDash:[6,3],pointRadius:0,fill:false,skipPill:true}
      ]},
      options:{responsive:true,maintainAspectRatio:false,
        // `layout.padding` reserva espaço em BRANCO ao redor da área de
        // plotagem pro pill do `lineLabel` (desenhado ~26px ACIMA de cada
        // ponto) nunca ficar cortado, mesmo pro ponto mais alto do eixo.
        layout:{padding:{top:36,right:28,bottom:4}},
        plugins:{legend:{position:"top"},lineLabel:{}},
        scales:{y:{beginAtZero:true,max:eixoMax,ticks:{callback:v=>v+" d"}},x:{ticks:{font:{size:11}}}}}
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
    const fmtReais=v=>"R$ "+v.toLocaleString("pt-BR",{minimumFractionDigits:2,maximumFractionDigits:2});
    mkChart("chartMidiaInvest",{type:"line",data:{labels:midia.meses,datasets:[
      {label:"Investimento BD (R$)",data:midia.investimento,borderColor:"#534AB7",backgroundColor:"rgba(83,74,183,0.15)",fill:true,tension:0.35,pointBackgroundColor:"#534AB7",pointRadius:6,pillFormat:fmtReais}
    ]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{position:"top"},pill:{}},
      scales:{y:{ticks:{callback:v=>"R$ "+v.toLocaleString("pt-BR")}},x:{ticks:{font:{size:11}}}}}});
  }

  // Aba 7 — chamados atendidos por operador (pedido do usuário 2026-08-03).
  // `operadorByMonth` só tem mês com chamado REAL do Portal: os meses de
  // baseline (jan-jun/26, controle fora do sistema) vêm vazios porque o
  // agregado histórico não guarda quem atendeu. Em vez de desenhar um gráfico
  // vazio sem explicação, a aba diz quais meses do período ficaram de fora.
  function agregarOperadores(labels){
    const agg={};
    labels.forEach(l=>{
      Object.entries(operadorByMonth[l]||{}).forEach(([nome,v])=>{
        if(!agg[nome]) agg[nome]={atendidos:0,resolvidos:0};
        agg[nome].atendidos+=v.atendidos;
        agg[nome].resolvidos+=v.resolvidos;
      });
    });
    return agg;
  }

  function renderOperador(){
    const labels=filteredMonthly().map(m=>m.label);
    const agg=agregarOperadores(labels);
    const nomes=Object.keys(agg).sort((a,b)=>agg[b].atendidos-agg[a].atendidos);
    mkChart("chartOperador",{type:"bar",data:{labels:nomes,datasets:[
      {label:"Atendidos (abertos no período)",data:nomes.map(n=>agg[n].atendidos),
       backgroundColor:"#534AB7",borderRadius:5},
      {label:"Resolvidos (fechados no período)",data:nomes.map(n=>agg[n].resolvidos),
       backgroundColor:"#1D9E75",borderRadius:5}
    ]},options:{indexAxis:"y",responsive:true,maintainAspectRatio:false,
      plugins:{legend:{position:"top"},pill:{}},
      scales:{x:{beginAtZero:true,ticks:{precision:0}},y:{ticks:{font:{size:11}}}}}});
  }

  function updateOperadorInsights(){
    const labels=filteredMonthly().map(m=>m.label);
    const agg=agregarOperadores(labels);
    const nomes=Object.keys(agg).sort((a,b)=>agg[b].total-agg[a].total);
    const atendidos=nomes.reduce((s,n)=>s+agg[n].atendidos,0);
    const resolvidos=nomes.reduce((s,n)=>s+agg[n].resolvidos,0);
    const orfaos=labels.reduce((s,l)=>s+(resolvidosSemOperador[l]||0),0);
    const setTexto=(id,txt)=>{const el=document.getElementById(id);if(el)el.textContent=txt;};

    setTexto("operador-atendidos",atendidos);
    setTexto("operador-atendidos-nota",labels.length===1?labels[0]:"Período selecionado");
    setTexto("operador-resolvidos",resolvidos);
    // A nota fecha a conta com as "Concluídas" do período em vez de mostrar um
    // percentual sobre "atendidos" — desde a correção de âncora os dois números
    // respondem perguntas diferentes (entrou no mês × foi fechado no mês) e a
    // razão entre eles não significava nada.
    setTexto("operador-resolvidos-nota", orfaos
      ? "+ "+orfaos+" sem operador = "+(resolvidos+orfaos)+" concluídas no período"
      : "Total de concluídas no período");
    setTexto("operador-qtd",nomes.length);
    if(nomes.length){
      setTexto("operador-top",nomes[0]);
      setTexto("operador-top-nota",agg[nomes[0]].atendidos+" atendidos · "+agg[nomes[0]].resolvidos+" resolvidos");
    }else{
      setTexto("operador-top","—");
      setTexto("operador-top-nota","Nenhum chamado com operador no período");
    }

    // Aviso dos meses pré-Portal. NÃO dá pra inferir isso de "não tem
    // operador": jan-jun/26 têm alguns chamados reais soltos no meio do
    // período de baseline, então o gráfico mostraria 2 atendidos num mês cujo
    // total é 43 — sem o aviso, parece que o time parou de trabalhar.
    // `m.baseline` vem do backend (ver AdminRepo.mkt_dashboard_data).
    const preSistema=filteredMonthly().filter(m=>m.baseline).map(m=>m.label);
    setTexto("operador-aviso", preSistema.length
      ? "Atenção: "+preSistema.join(" · ")+(preSistema.length===1?" é anterior":" são anteriores")
        + " ao uso do Portal pelo Marketing — o total do mês veio do controle externo, que não registra quem atendeu."
        + " O que aparece aqui desses meses são só os poucos chamados abertos no Portal na época."
      : "");
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
    
    const tempoW = mediaTempoPonderada(d);
    document.getElementById("tempo-media-acumulado").textContent = fmtDias(tempoW);
    
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
      if (m.concluidas > 0 && m.tempo_medio != null && m.tempo_medio < melhorMedia) {
        melhorMedia = m.tempo_medio;
        melhorMediaMes = m.label;
      }
    });
    document.getElementById("tempo-melhor-media").textContent = melhorMedia === 999.0 ? "—" : fmtDias(melhorMedia);
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
    const id = activeTabBtn ? activeTabBtn.dataset.tab : "entrega";
    
    if(id==="volume"){renderVolume();updateVolumeInsights();}
    else if(id==="origem")renderOrigem();
    else if(id==="dept")renderDept();
    else if(id==="tempo"){renderTempo();buildAtrasosTable();renderCausas();updateTempoInsights();}
    else if(id==="midia"){renderMidia();updateMidiaInsights();}
    else if(id==="operador"){renderOperador();updateOperadorInsights();}
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
    else if(name==="volume"){renderVolume();updateVolumeInsights();}
    else if(name==="origem")renderOrigem();
    else if(name==="dept")renderDept();
    else if(name==="tempo"){renderTempo();buildAtrasosTable();renderCausas();updateTempoInsights();}
    else if(name==="midia"){renderMidia();updateMidiaInsights();}
    else if(name==="operador"){renderOperador();updateOperadorInsights();}
  }

  // ─── EVENTOS ──────────────────────────────────────────────────────────
  // CSP do painel é `script-src 'self'` sem `unsafe-inline` (app/security/
  // headers.py) — atributo `onclick="..."` no HTML é bloqueado pelo navegador
  // (era o motivo dos filtros/abas não responderem a clique). Os botões
  // carregam `data-filter`/`data-tab`; a escuta é registrada aqui.
  document.querySelectorAll(".filter-pill").forEach(btn => {
    btn.addEventListener("click", () => setFilter(btn.dataset.filter, btn));
  });
  document.querySelectorAll(".tab-btn").forEach(btn => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab, btn));
  });

  // ─── INIT ─────────────────────────────────────────────────────────────
  // Só a aba visível (Taxa de Entrega) é renderizada aqui — as outras
  // (canvas dentro de `.tab-panel{display:none}`) só ganham o Chart.js
  // quando `switchTab` as torna visíveis. Chart.js mede a caixa do canvas na
  // hora de criar o chart: um canvas ainda `display:none` tem largura/altura
  // zero, e "aparecer" depois (display:block) não corrige — o gráfico fica
  // com posicionamento/labels quebrados mesmo depois de visível (era o motivo
  // dos gráficos das abas 2-6 saírem com números flutuando fora do lugar).
  // `switchTab` já chama o render certo no clique, então isso nunca fica
  // vazio — só espera a aba ficar visível ANTES de desenhar.
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
