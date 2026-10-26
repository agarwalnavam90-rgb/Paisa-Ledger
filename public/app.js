/* The browser only formats and displays values; financial calculations live in Python models. */
const $ = (selector) => document.querySelector(selector);
const money = (value) => new Intl.NumberFormat('en-IN', {style:'currency',currency:'INR',maximumFractionDigits:2}).format(Number(value)||0);
const dateLabel = (value) => value ? new Intl.DateTimeFormat('en-IN',{day:'2-digit',month:'short',year:'numeric'}).format(new Date(`${value}T00:00:00`)) : '—';
const escapeHTML = (value='') => String(value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const charts = {};
let allTransactions = [];
async function api(url, options={}) {
  const response = await fetch(url, {...options, headers:{...(options.body?{'Content-Type':'application/json'}:{}), ...(options.headers||{})}});
  const isJson = (response.headers.get('content-type')||'').includes('application/json');
  let body;
  try { body = isJson ? await response.json() : await response.blob(); }
  catch { throw new Error('The server returned an unreadable response. Please try again.'); }
  if (!response.ok) throw new Error(isJson ? body.error || 'Request failed.' : 'Request failed.');
  return body;
}
function showMessage(message, isError=true) {
  const banner=$('#app-message');
  banner.textContent=message||''; banner.hidden=!message;
  banner.classList.toggle('error',Boolean(message&&isError));
}
function setBusy(button, busy, label='Working…') {
  if (!button) return;
  if (busy) { button.dataset.originalLabel=button.textContent; button.textContent=label; }
  else if (button.dataset.originalLabel) { button.textContent=button.dataset.originalLabel; delete button.dataset.originalLabel; }
  button.disabled=busy;
}
function setChart(id, config) {
  if (!window.Chart) return;
  if (charts[id]) charts[id].destroy();
  const canvas = document.getElementById(id); if (!canvas) return;
  charts[id] = new Chart(canvas, config);
}
const chartBase = {responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{callbacks:{label:(ctx)=>`${ctx.dataset.label||ctx.label}: ${money(ctx.raw)}`}}},scales:{x:{grid:{display:false},ticks:{font:{family:'IBM Plex Mono',size:9},color:'#746f64'}},y:{beginAtZero:true,grid:{color:'rgba(28,26,23,.09)'},ticks:{font:{family:'IBM Plex Mono',size:9},color:'#746f64',callback:(v)=>`₹${Number(v).toLocaleString('en-IN')}`}}}};
function rowHTML(tx, actions=false) {
  const sign = tx.type === 'income' ? '+' : '−';
  return `<tr><td>${dateLabel(tx.date)}</td><td>${escapeHTML(tx.description)}${tx.notes?`<div class="type-label">${escapeHTML(tx.notes)}</div>`:''}</td><td>${escapeHTML(tx.category)}</td><td>${escapeHTML(tx.payment_method)}</td><td class="numeric ${tx.type==='income'?'amount-income':'amount-expense'}">${sign}${money(tx.amount)}</td>${actions?`<td class="action-cell"><button data-edit="${escapeHTML(tx.id)}">Edit</button><button class="delete" data-delete="${escapeHTML(tx.id)}">Delete</button></td>`:''}</tr>`;
}
function emptyRow(columns, message='No transactions found.') { return `<tr><td colspan="${columns}" class="empty-row">${message}</td></tr>`; }
async function loadDashboardSummary() {
  $('#balance').textContent='Loading…'; $('#month-income').textContent='Loading…';
  $('#month-expenses').textContent='Loading…'; $('#month-savings').textContent='Loading…';
  const d = await api('/api/dashboard');
  $('#today-label').textContent = new Intl.DateTimeFormat('en-IN',{day:'2-digit',month:'short',year:'numeric'}).format(new Date());
  $('#dashboard-month').textContent = d.month;
  $('#balance').textContent=money(d.balance); $('#month-income').textContent=money(d.income); $('#month-expenses').textContent=money(d.expenses);
  $('#month-savings').textContent=money(d.net_savings); $('#savings-rate').textContent=`${d.savings_rate}%`; $('#transaction-count').textContent=d.transaction_count;
  const cashOptions=structuredClone(chartBase); cashOptions.plugins={...cashOptions.plugins,legend:{display:true,position:'bottom',labels:{boxWidth:8,font:{family:'IBM Plex Mono',size:9},color:'#746f64'}}};
  setChart('cashflow-chart',{type:'bar',data:{labels:d.months,datasets:[{label:'Income',data:d.monthly_income,backgroundColor:'#1F5A45'},{label:'Expenses',data:d.monthly_expenses,backgroundColor:'#b9ad98'}]},options:cashOptions});
  const cats=Object.entries(d.category_breakdown); const palette=['#1F5A45','#a88d62','#6f7863','#b45e42','#c5b897','#343d31','#8c7760'];
  setChart('category-chart',{type:'doughnut',data:{labels:cats.map(x=>x[0]),datasets:[{data:cats.map(x=>x[1]),backgroundColor:cats.map((_,i)=>palette[i%palette.length]),borderColor:'#fbf8f0',borderWidth:3,hoverOffset:4}]},options:{responsive:true,maintainAspectRatio:false,cutout:'68%',plugins:{legend:{display:false},tooltip:{callbacks:{label:(ctx)=>`${ctx.label}: ${money(ctx.raw)}`}}}}});
  $('#category-legend').innerHTML=cats.length?cats.slice(0,6).map(([name,val],i)=>`<span class="legend-item"><i class="legend-swatch" style="background:${palette[i%palette.length]}"></i>${escapeHTML(name)} · ${money(val)}</span>`).join(''):'<span class="type-label">No expenses recorded this month.</span>';
}
async function loadRecentTransactions() {
  $('#recent-body').innerHTML='<tr><td colspan="5" class="empty-row loading-state">Loading recent activity…</td></tr>';
  const tx=await api('/api/transactions'); allTransactions=tx.transactions;
  $('#recent-body').innerHTML=allTransactions.slice(0,5).map(t=>rowHTML(t)).join('')||emptyRow(5,'Your ledger is empty. Add your first transaction.');
}
async function loadDashboard() {
  const results=await Promise.allSettled([loadDashboardSummary(),loadRecentTransactions()]);
  if(results[0].status==='rejected'){
    ['#balance','#month-income','#month-expenses','#month-savings','#savings-rate','#transaction-count'].forEach(selector=>$(selector).textContent='Unavailable');
    showError(results[0].reason);
  }
  if(results[1].status==='rejected')showError(results[1].reason);
}
async function loadTransactions(filters={}) {
  $('#transactions-body').innerHTML='<tr><td colspan="6" class="empty-row loading-state">Loading ledger entries…</td></tr>';
  const params=new URLSearchParams(); Object.entries(filters).forEach(([k,v])=>{if(v)params.set(k,v)});
  try {
    const data=await api(`/api/transactions${params.size?'?'+params:''}`);
    $('#transactions-body').innerHTML=data.transactions.map(t=>rowHTML(t,true)).join('')||emptyRow(6);
  } catch(error) {
    $('#transactions-body').innerHTML=emptyRow(6,'The ledger could not be loaded. Please try again.');
    throw error;
  }
}
async function loadBudgets() {
  $('#budget-list').innerHTML='<article class="paper empty-state loading-state">Loading budgets…</article>';
  try {
    const {budgets}=await api('/api/budgets');
    $('#budget-list').innerHTML=budgets.length?budgets.map(b=>{const pct=Math.min(100,b.percentage_used);return `<article class="budget-card ${b.over_budget?'over':''}"><div class="budget-title"><div><h3>${escapeHTML(b.category)}</h3><span class="budget-period">${escapeHTML(b.month)} MONTHLY LIMIT</span></div>${b.over_budget?'<span class="warning">OVER BUDGET</span>':''}</div><div class="budget-spend"><strong>${money(b.spent)}</strong> of ${money(b.limit)}</div><div class="progress-track"><div class="progress-fill" style="width:${pct}%"></div></div><div class="budget-bottom"><span>${b.percentage_used}% used</span><span class="${b.over_budget?'warning':''}">${b.remaining>=0?money(b.remaining)+' remaining':money(-b.remaining)+' over'}</span></div></article>`}).join(''):'<article class="paper empty-state">No budgets yet. Set a monthly category limit above.</article>';
  } catch(error) {
    $('#budget-list').innerHTML='<article class="paper empty-state">Budgets could not be loaded. Please try again.</article>';
    throw error;
  }
}
async function loadAnalytics() {
  $('#analysis-metrics').innerHTML='<div class="analysis-stat loading-state">Loading analysis…</div>';
  let a;
  try { a=await api('/api/analytics'); }
  catch(error) {
    $('#analysis-metrics').innerHTML='<div class="analysis-stat">Analytics could not be loaded. Please try again.</div>';
    $('#analysis-categories').textContent='Category analysis is unavailable.';
    $('#month-comparison').textContent='Month comparison is unavailable.';
    throw error;
  }
  const entries=Object.entries(a.monthly);
  const largest=a.largest_expense;
  $('#analysis-metrics').innerHTML=`<div class="analysis-stat"><div class="metric-label">HIGHEST SPEND CATEGORY</div><strong>${escapeHTML(a.highest_expense_category||'—')}</strong></div><div class="analysis-stat"><div class="metric-label">LARGEST SINGLE EXPENSE</div><strong>${largest?money(largest.amount):money(0)}</strong></div><div class="analysis-stat"><div class="metric-label">AVERAGE MONTHLY EXPENSES</div><strong>${money(a.average_monthly_expenses)}</strong><div class="metric-foot">From first to last ledger month, gaps included</div></div><div class="analysis-stat"><div class="metric-label">OVERALL SAVINGS RATE</div><strong>${Number(a.overall_savings_rate).toFixed(1)}%</strong><div class="metric-foot">Monthly-rate mean: ${Number(a.average_monthly_savings_rate).toFixed(1)}%</div></div>`;
  setChart('savings-chart',{type:'line',data:{labels:entries.map(x=>x[0]),datasets:[{label:'Net savings',data:entries.map(x=>x[1].savings),borderColor:'#1F5A45',backgroundColor:'rgba(31,90,69,.12)',fill:true,tension:.2,pointRadius:3,pointBackgroundColor:'#1F5A45'}]},options:structuredClone(chartBase)});
  const cats=Object.entries(a.category_breakdown);const max=Math.max(1,...cats.map(x=>x[1]));
  $('#analysis-categories').innerHTML=cats.length?cats.map(([name,val])=>`<div class="analysis-cat"><span>${escapeHTML(name)}</span><span class="bar"><i style="width:${val/max*100}%"></i></span><span>${money(val)}</span></div>`).join(''):'<span class="type-label">No expense categories yet.</span>';
  const c=a.month_comparison;
  $('#month-comparison').innerHTML=`<div class="eyebrow">MONTH OVER MONTH</div><h2>${c.current_month} <span class="type-label">vs</span> ${c.previous_month}</h2><div class="comparison-values"><span>Income<strong>${money(c.current.income)} <small>(${c.income_change>=0?'+':''}${money(c.income_change)})</small></strong></span><span>Expenses<strong>${money(c.current.expenses)} <small>(${c.expense_change>=0?'+':''}${money(c.expense_change)})</small></strong></span><span>Net savings<strong>${money(c.current.savings)}</strong></span></div>`;
}
async function loadClassTree() {
  let classes;
  try { ({classes}=await api('/api/classes')); }
  catch(error) {
    $('#class-tree').innerHTML='<p class="type-label">The class map could not be loaded. Please try again.</p>';
    throw error;
  }
  const order=['Transaction','IncomeTransaction','ExpenseTransaction','Account','Budget','FinancialAnalyzer','StatementGenerator'];
  const sorted=order.map(n=>classes.find(c=>c.name===n)).filter(Boolean);
  $('#class-tree').innerHTML=sorted.map(c=>{const flowMethods=Object.entries(c.method_highlights||{}).flatMap(([flow,names])=>names.map(name=>[name,flow]));return `<article class="class-card ${c.parent?'child':''}"><div class="class-header"><h2>${escapeHTML(c.name)}</h2><span class="parent-tag">${c.parent?`extends ${escapeHTML(c.parent)}`:'root class'}</span></div><p class="class-purpose">${escapeHTML(c.purpose)}</p><div class="class-meta"><div><b>ATTRIBUTES</b>${c.attributes.map(x=>`<span class="tag">${escapeHTML(x)}</span>`).join(' ')}</div><div><b>IMPORTANT METHODS${c.overrides.length?' · OVERRIDES IN GREEN':''}</b><div class="tag-list">${c.methods.map(x=>{const flows=flowMethods.filter(([name])=>name===x).map(([,flow])=>flow);return `<span class="tag ${c.overrides.includes(x)||flows.length?'highlight':''}" title="${flows.join(', ')}">${escapeHTML(x)}${c.overrides.includes(x)?' ↻':''}</span>`}).join('')}</div></div><div><b>OBJECT RELATIONSHIPS</b>${c.relationships.map(x=>`<div>${escapeHTML(x)}</div>`).join('')}<b style="margin-top:7px">USED IN FLOWS</b><div class="tag-list">${c.highlights.map(x=>`<span class="tag highlight">${escapeHTML(x)}</span>`).join('')}</div></div></div></article>`}).join('');
}
function navigate(view) {
  showMessage('',false);
  document.querySelectorAll('.nav-link').forEach(b=>b.classList.toggle('active',b.dataset.view===view));
  document.querySelectorAll('.view').forEach(s=>s.classList.toggle('active',s.id===`view-${view}`));
  if(view==='transactions')loadTransactions().catch(showError); if(view==='budgets')loadBudgets().catch(showError); if(view==='analytics')loadAnalytics().catch(showError);
  if(view==='underhood')loadClassTree().catch(showError); if(view==='dashboard')loadDashboard().catch(showError);
  window.scrollTo({top:0,behavior:'smooth'});
}
function showError(error){console.error(error);showMessage(error?.message||'Something went wrong. Please try again.',true);}
async function refreshAfterTransaction() {
  const tasks=[loadDashboard(),loadAnalytics()];
  if ($('#view-transactions').classList.contains('active')) tasks.push(loadTransactions());
  if ($('#view-budgets').classList.contains('active')) tasks.push(loadBudgets());
  const results=await Promise.allSettled(tasks);
  results.filter(result=>result.status==='rejected').forEach(result=>showError(result.reason));
}
function openTransaction(tx=null) {
  const dialog=$('#transaction-dialog');$('#transaction-form').reset();$('#form-error').textContent='';$('#transaction-id').value=tx?.id||'';
  $('#form-title').textContent=tx?'Edit transaction':'Add transaction';$('#description').value=tx?.description||'';$('#amount').value=tx?.amount||'';$('#type').value=tx?.type||'expense';$('#category').value=tx?.category||'';$('#date').value=tx?.date||new Date().toISOString().slice(0,10);$('#payment-method').value=tx?.payment_method||'UPI';$('#notes').value=tx?.notes||'';
  dialog.showModal();
}
document.querySelectorAll('.nav-link').forEach(b=>b.addEventListener('click',()=>navigate(b.dataset.view)));
document.querySelectorAll('[data-view-link]').forEach(b=>b.addEventListener('click',()=>navigate(b.dataset.viewLink)));
document.querySelectorAll('[data-action="new-transaction"]').forEach(b=>b.addEventListener('click',()=>openTransaction()));
$('#add-transaction').addEventListener('click',()=>openTransaction());
$('#close-dialog').addEventListener('click',()=>$('#transaction-dialog').close());$('#cancel-form').addEventListener('click',()=>$('#transaction-dialog').close());
$('#transaction-form').addEventListener('submit',async event=>{event.preventDefault();const form=event.currentTarget;if(form.dataset.busy)return;form.dataset.busy='true';const submit=event.submitter||form.querySelector('button[type="submit"]');const id=$('#transaction-id').value;const payload={description:$('#description').value,amount:Number($('#amount').value),type:$('#type').value,category:$('#category').value,date:$('#date').value,payment_method:$('#payment-method').value,notes:$('#notes').value};$('#form-error').textContent='';setBusy(submit,true,'Saving…');try{await api(id?`/api/transactions/${encodeURIComponent(id)}`:'/api/transactions',{method:id?'PUT':'POST',body:JSON.stringify(payload)});$('#transaction-dialog').close();showMessage(id?'Transaction updated.':'Transaction added.',false);await refreshAfterTransaction();}catch(error){$('#form-error').textContent=error.message;showMessage(error.message,true);}finally{delete form.dataset.busy;setBusy(submit,false);}});
$('#transactions-body').addEventListener('click',async event=>{const edit=event.target.dataset.edit,del=event.target.dataset.delete;if(edit){try{const {transactions}=await api('/api/transactions');const transaction=transactions.find(t=>t.id===edit);if(transaction)openTransaction(transaction);else showError(new Error('That transaction is no longer in the ledger.'));}catch(e){showError(e)}}if(del&&confirm('Delete this transaction from the ledger?')){const button=event.target;setBusy(button,true,'Deleting…');try{await api(`/api/transactions/${encodeURIComponent(del)}`,{method:'DELETE'});showMessage('Transaction deleted.',false);await refreshAfterTransaction();}catch(e){showError(e)}finally{setBusy(button,false)}}});
$('#apply-filters').addEventListener('click',async event=>{const button=event.currentTarget;setBusy(button,true,'Loading…');try{await loadTransactions({start_date:$('#filter-start').value,end_date:$('#filter-end').value,category:$('#filter-category').value.trim(),type:$('#filter-type').value});}catch(error){showError(error);}finally{setBusy(button,false)}});
$('#clear-filters').addEventListener('click',async event=>{$('#filter-start').value='';$('#filter-end').value='';$('#filter-category').value='';$('#filter-type').value='';const button=event.currentTarget;setBusy(button,true,'Loading…');try{await loadTransactions();}catch(error){showError(error);}finally{setBusy(button,false)}});
$('#budget-month').value=new Date().toISOString().slice(0,7);
$('#budget-form').addEventListener('submit',async event=>{event.preventDefault();const form=event.currentTarget;if(form.dataset.busy)return;form.dataset.busy='true';const submit=event.submitter||form.querySelector('button[type="submit"]');$('#budget-error').textContent='';setBusy(submit,true,'Saving…');try{await api('/api/budgets',{method:'POST',body:JSON.stringify({category:$('#budget-category').value,month:$('#budget-month').value,limit:Number($('#budget-limit').value)})});$('#budget-form').reset();$('#budget-month').value=new Date().toISOString().slice(0,7);showMessage('Budget saved.',false);const results=await Promise.allSettled([loadBudgets(),loadDashboardSummary()]);results.filter(result=>result.status==='rejected').forEach(result=>showError(result.reason));}catch(error){$('#budget-error').textContent=error.message;showMessage(error.message,true);}finally{delete form.dataset.busy;setBusy(submit,false);}});
$('#statement-form').addEventListener('submit',async event=>{event.preventDefault();const form=event.currentTarget;if(form.dataset.busy)return;form.dataset.busy='true';const submit=event.submitter||form.querySelector('button[type="submit"]');$('#statement-error').textContent='';const start_date=$('#statement-start').value,end_date=$('#statement-end').value;setBusy(submit,true,'Preparing PDF…');try{const blob=await api('/api/statements/pdf',{method:'POST',body:JSON.stringify({start_date,end_date})});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=`paisaledger-statement-${start_date}-to-${end_date}.pdf`;a.click();URL.revokeObjectURL(url);showMessage('Statement downloaded.',false);}catch(error){$('#statement-error').textContent=error.message;showMessage(error.message,true);}finally{delete form.dataset.busy;setBusy(submit,false);}});
$('#reset-demo').addEventListener('click',async event=>{if(!confirm('Restore the original PaisaLedger sample transactions and budgets? Your current local demo data will be replaced.'))return;const button=event.currentTarget;setBusy(button,true,'Resetting…');try{await api('/api/reset-demo',{method:'POST'});showMessage('Demo data restored.',false);const results=await Promise.allSettled([loadDashboard(),loadAnalytics(),loadBudgets(),loadTransactions(),loadClassTree()]);results.filter(result=>result.status==='rejected').forEach(result=>showError(result.reason));}catch(error){showError(error);}finally{setBusy(button,false);}});
loadDashboard().catch(showError);
loadClassTree().catch(showError);
