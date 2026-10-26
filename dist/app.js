'use strict';

const $ = id => document.getElementById(id);
const csrf = document.querySelector('meta[name="ecnplus-token"]').content;
const number = value => new Intl.NumberFormat('fr-TN').format(value ?? 0);
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icon = name => `<svg class="icon" aria-hidden="true"><use href="#i-${name}"/></svg>`;
const normalize = text => text.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/œ/g, 'oe');
const dateFormat = new Intl.DateTimeFormat('fr-TN', {timeZone: 'Africa/Tunis', day: 'numeric', month: 'short', year: 'numeric'});
const syncFormat = new Intl.DateTimeFormat('fr-TN', {timeZone: 'Africa/Tunis', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'});
const state = {data: null, focus: null, detail: null, detailReading: 'all', poll: null, toast: null, lastFinished: null};

async function api(path, body) {
  let response;
  try {
    response = await fetch(path, {cache: 'no-store', method: body === undefined ? 'GET' : 'POST',
      headers: body === undefined ? {} : {'Content-Type': 'application/json', 'X-ECNPlus-Token': csrf},
      ...(body === undefined ? {} : {body: JSON.stringify(body)})});
  } catch {
    throw new Error('Impossible de joindre le tableau de bord local. Vérifiez que son terminal est toujours ouvert.');
  }
  let data;
  try { data = await response.json(); }
  catch { throw new Error('Le tableau de bord a renvoyé une réponse invalide. Rechargez la page et réessayez.'); }
  if (!response.ok) throw new Error(data.error || 'Une erreur est survenue. Veuillez réessayer.');
  return data;
}

function activeReadings() {
  return (state.data?.summary?.settings || state.data?.settings)?.readings.filter((r, i) => i === 0 || r.starts_on) || [];
}

function shortDate(value) {
  return new Intl.DateTimeFormat('fr-TN', {timeZone:'Africa/Tunis', day:'numeric', month:'short'}).format(new Date(value + 'T12:00:00Z'));
}

function period(reading, index, readings) {
  const next = readings[index + 1]?.starts_on;
  const end = next ? new Date(new Date(next + 'T12:00:00Z').getTime() - 86400000).toISOString().slice(0, 10) : null;
  if (!reading.starts_on) return end ? `Jusqu’au ${shortDate(end)}` : 'Toute activité antérieure';
  return end ? `Du ${shortDate(reading.starts_on)} au ${shortDate(end)}` : `À partir du ${shortDate(reading.starts_on)}`;
}

function toast(message) {
  clearTimeout(state.toast);
  $('toast').textContent = message;
  $('toast').hidden = false;
  state.toast = setTimeout(() => {$('toast').hidden = true;}, 4500);
}

function notice(message, error = false, action = null) {
  $('notice').hidden = !message;
  $('notice').classList.toggle('error', error);
  $('notice').replaceChildren(document.createTextNode(message || ''));
  if (action) {
    const button = document.createElement('button');
    button.textContent = action.label;
    button.addEventListener('click', action.run);
    $('notice').append(button);
  }
}

function overview() {
  const summary = state.data.summary;
  const readings = activeReadings();
  $('overview-stats').innerHTML = readings.map((reading, i) => {
    const counts = summary?.readings[reading.id];
    return `<article class="stat-card reading-${i + 1}"><div class="stat-top"><span class="stat-label"><span class="reading-dot"></span>${escapeHTML(reading.name)}</span>${reading.id === state.data.current_reading ? '<span class="current-pill">EN COURS</span>' : ''}</div><div class="stat-period">${escapeHTML(period(reading, i, readings))}</div><div class="stat-number">${counts ? number(counts.answered_questions) : '—'}</div><span class="stat-number-label">questions répondues</span><div class="stat-bottom">${icon('check')}<strong>${counts ? number(counts.completed_series) : '—'}</strong> séries terminées</div></article>`;
  }).join('') + `<article class="stat-card stat-total"><div class="stat-top"><span class="stat-label">Toutes les lectures</span>${icon('book')}</div><div class="stat-period">Total de votre travail</div><div class="stat-number">${summary ? number(summary.total.answered_questions) : '—'}</div><span class="stat-number-label">questions répondues</span><div class="stat-bottom">${icon('check')}<strong>${summary ? number(summary.total.completed_series) : '—'}</strong> séries terminées</div></article>`;
  $('overview-stats').classList.toggle('many-readings', readings.length > 3);
}

function renderCourses() {
  const summary = state.data?.summary;
  const readings = activeReadings();
  const query = normalize($('search').value.trim());
  const filter = $('filter').value;
  const sort = $('sort').value;
  const all = summary?.courses || [];
  const counts = course => course.readings[state.focus] || {answered_questions:0, completed_series:0, in_progress_series:0};
  const courses = all.filter(course => {
    const focused = counts(course);
    return normalize(course.name).includes(query) && (filter === 'all' ||
      filter === 'not-started' && focused.answered_questions === 0 ||
      filter === 'active' && focused.answered_questions > 0 ||
      filter === 'unfinished' && focused.in_progress_series > 0);
  });
  courses.sort((a,b) => {
    let difference = 0;
    if (sort === 'least') difference = counts(a).answered_questions - counts(b).answered_questions;
    if (sort === 'most') difference = counts(b).answered_questions - counts(a).answered_questions;
    if (sort === 'series') difference = counts(b).completed_series - counts(a).completed_series;
    return difference || a.name.localeCompare(b.name, 'fr');
  });
  $('course-grid').setAttribute('aria-busy', 'false');
  $('course-grid').innerHTML = courses.map(course => `<article class="course-card"><button class="card-open" data-course="${escapeHTML(course.id)}" aria-label="Voir le détail de ${escapeHTML(course.name)}"><span class="card-heading"><span class="course-icon">${icon('book')}</span><span class="course-name">${escapeHTML(course.name)}</span></span><span class="card-table-heading"><span>Lecture</span><span>Séries terminées</span><span>Questions</span></span>${readings.map((r, i) => {
    const values = course.readings[r.id];
    return `<span class="reading-row reading-${i+1} ${r.id === state.focus ? 'focused' : ''}"><span class="reading-name"><span class="reading-dot"></span>${escapeHTML(r.name)}</span><span class="series-count ${!values.completed_series ? 'zero' : ''}">${number(values.completed_series)}</span><span class="questions-count ${!values.answered_questions ? 'zero' : ''}">${number(values.answered_questions)}</span></span>`;
  }).join('')}${course.totals_status === 'provisional' ? '<span class="provisional-dot">Certaines données doivent être mises à jour ou vérifiées</span>' : ''}<span class="card-total"><span>Total</span><strong>${number(course.total.completed_series)}</strong> séries terminées <span>·</span><strong>${number(course.total.answered_questions)}</strong> questions répondues ${icon('arrow')}</span></button></article>`).join('');
  $('course-total').textContent = number(all.length);
  $('nav-count').textContent = number(all.length);
  const focusName = readings.find(r => r.id === state.focus)?.name;
  $('results-label').textContent = summary ? `${number(courses.length)} cours sur ${number(all.length)}${focusName ? ' · Filtres et tri selon « ' + focusName.toLowerCase() + ' »' : ''}` : '';
  $('empty-state').hidden = courses.length > 0;
  $('reset-filters').hidden = !all.length;
  if (!all.length) {
    $('empty-title').textContent = 'Votre espace de révision est prêt';
    $('empty-description').textContent = 'Ajoutez votre jeton ECNPlus dans les paramètres, puis synchronisez vos cours.';
  } else {
    $('empty-title').textContent = 'Aucun cours trouvé';
    $('empty-description').textContent = 'Essayez une autre recherche, sélectionnez une autre lecture ou modifiez les filtres.';
  }
}

async function load() {
  const data = await api('/api/dashboard');
  state.data = data;
  const readings = activeReadings();
  if (!readings.some(r => r.id === state.focus)) state.focus = data.current_reading;
  $('focus-reading').innerHTML = readings.map(r => `<option value="${r.id}">${escapeHTML(r.name)}</option>`).join('');
  $('focus-reading').value = state.focus;
  $('intro').textContent = data.summary ? `${number(data.summary.courses.length)} cours. Toutes les lectures. Au même endroit.` : 'Tous les cours et toutes les lectures réunis au même endroit.';
  $('save-state').textContent = data.summary ? 'Données enregistrées localement' : 'Prêt pour la première synchronisation';
  $('last-sync').textContent = data.collection.last_sync ? `Dernière synchronisation : ${syncFormat.format(new Date(data.collection.last_sync))}` : 'Aucune donnée chargée à l’ouverture';
  $('connection-state').textContent = data.credential_configured ? 'Jeton configuré' : 'Jeton requis';
  overview();
  renderCourses();
  const checkpoint = data.checkpoint;
  if (data.settings_changed) {
    notice('Les dates de lecture ont changé. Appliquez-les pour mettre à jour les totaux enregistrés.', false, {label:'Appliquer les dates', run: () => startSettings(data.settings.readings).catch(error => notice(error.message, true))});
  } else if (checkpoint && checkpoint.status !== 'complete' && data.job.status !== 'running') {
    notice('La dernière synchronisation est incomplète. Votre progression enregistrée reste disponible.', true, {label:'Reprendre la synchronisation', run: () => startSync(true)});
  } else if (data.summary && !data.collection.phase_ready) {
    notice('Certaines données enregistrées sont incomplètes ou doivent être vérifiées. La progression disponible est affichée ci-dessous.', true);
  } else {
    notice(null);
  }
  renderJob(data.job);
  if (data.job.status === 'running') pollJob();
  if (data.job.status === 'failed') showJobFailure(data.job);
}

function showJobFailure(job) {
  const unfinished = state.data?.checkpoint && state.data.checkpoint.status !== 'complete';
  const action = job.kind === 'settings' || /token|account/i.test(job.message)
    ? {label:'Ouvrir les paramètres', run:openSettings}
    : unfinished ? {label:'Reprendre la synchronisation', run:() => startSync(true)}
    : {label:'Réessayer', run:() => startSync(false)};
  notice(job.message, true, action);
}

function renderJob(job) {
  if (state.data) state.data.job = job;
  const running = job.status === 'running';
  $('job-panel').hidden = !running;
  $('sync-button').disabled = running;
  $('save-settings').disabled = running;
  $('save-credential').disabled = running;
  $('sync-button').querySelector('span').textContent = running ? (job.kind === 'settings' ? 'Recalcul en cours…' : 'Synchronisation…') : 'Synchroniser';
  $('sync-button').querySelector('svg').classList.toggle('spinning', running);
  if (running) {
    $('job-message').textContent = job.kind === 'settings' ? 'Recalcul des totaux par lecture…' : job.message;
    $('job-count').textContent = job.total ? `${number(job.processed)} / ${number(job.total)}` : '';
    if (job.total) { $('job-progress').max = job.total; $('job-progress').value = job.processed; }
    else $('job-progress').removeAttribute('value');
  }
}

function pollJob() {
  if (state.poll) return;
  let failures = 0;
  const next = async () => {
    try {
      const job = await api('/api/job');
      failures = 0;
      renderJob(job);
      if (job.status === 'running') { state.poll = setTimeout(next, 1000); return; }
      state.poll = null;
      await load();
      if (job.status === 'succeeded' && state.lastFinished !== job.finished_at) {
        state.lastFinished = job.finished_at;
        if (job.kind === 'settings') $('settings-dialog').close();
        toast(job.kind === 'settings' ? 'Dates enregistrées. Vos totaux ont été recalculés.' : 'Synchronisation terminée. Votre progression est à jour.');
      } else if (job.status === 'failed') {
        showJobFailure(job);
        if ($('settings-dialog').open) { $('settings-error').textContent = job.message; $('settings-error').hidden = false; }
      }
    } catch (error) {
      failures++;
      if (failures < 4) { state.poll = setTimeout(next, 1500); return; }
      state.poll = null;
      notice('Connexion perdue. Laissez le terminal du tableau de bord ouvert, puis rechargez cette page.', true);
    }
  };
  state.poll = setTimeout(next, 500);
}

async function startSync(resume = false) {
  if (!state.data?.credential_configured) { openSettings(); toast('Ajoutez votre jeton ECNPlus pour lancer la synchronisation.'); $('credential').focus(); return; }
  try { notice(null); renderJob(await api('/api/sync', {resume})); pollJob(); }
  catch (error) { notice(error.message, true); }
}

async function startSettings(readings) {
  const job = await api('/api/settings', {readings});
  renderJob(job);
  pollJob();
}

function openSettings() {
  if (!state.data) return;
  $('settings-error').hidden = true;
  $('credential-error').hidden = true;
  $('credential').value = '';
  $('credential-note').textContent = 'Enregistré uniquement sur cet ordinateur. Votre jeton ne sera jamais affiché.';
  $('reading-fields').innerHTML = state.data.settings.readings.map((reading, i) => `<div class="reading-setting-row"><input type="text" name="name-${reading.id}" aria-label="Nom de la lecture ${i + 1}" value="${escapeHTML(reading.name)}" required maxlength="80">${i ? `<input type="date" name="date-${reading.id}" aria-label="Date de début de la lecture ${i + 1}" value="${escapeHTML(reading.starts_on || '')}">` : '<span class="first-period">Toute activité antérieure</span>'}</div>`).join('');
  if (!$('settings-dialog').open) $('settings-dialog').showModal();
}

async function openCourse(id) {
  $('detail-title').textContent = state.data.summary.courses.find(c => c.id === id)?.name || 'Détail du cours';
  $('detail-content').innerHTML = '<p class="detail-empty">Chargement des tentatives enregistrées…</p>';
  state.detail = null;
  if (!$('course-dialog').open) $('course-dialog').showModal();
  try {
    const detail = await api('/api/courses/' + encodeURIComponent(id));
    if (!$('course-dialog').open || $('detail-title').textContent !== detail.course.name) return;
    state.detail = detail;
    state.detailReading = 'all';
    renderDetail();
  } catch (error) { $('detail-content').textContent = error.message; }
}

function renderDetail() {
  const {course, attempts, settings} = state.detail;
  const readings = settings.readings.filter((r, i) => i === 0 || r.starts_on);
  const names = Object.fromEntries(readings.map(r => [r.id, r.name]));
  const filtered = attempts.filter(a => state.detailReading === 'all' || a.reading_id === state.detailReading);
  $('detail-content').innerHTML = `<div class="detail-overview"><strong>${number(course.total.answered_questions)}</strong><span>questions répondues</span><span>${number(course.total.completed_series)} séries terminées · ${number(course.total.in_progress_series)} en cours</span></div><div class="detail-tabs" role="group" aria-label="Filtrer les tentatives par lecture">${[{id:'all', name:'Toutes les lectures'}, ...readings].map(r => `<button type="button" data-detail-reading="${r.id}" aria-pressed="${r.id === state.detailReading}" class="${r.id === state.detailReading ? 'active' : ''}">${escapeHTML(r.name)}</button>`).join('')}</div><p class="detail-note">Pour une même série au cours d’une lecture, le nombre maximal de questions répondues est retenu. Les autres tentatives restent visibles à titre indicatif. Les dates affichées sont les dates de début, selon l’heure de la Tunisie.</p>${filtered.length ? `<div class="table-wrap"><table class="attempt-table"><thead><tr><th>Début</th><th>Lecture</th><th>Questions répondues</th><th>État</th><th><span class="sr-only">Lien ECNPlus</span></th></tr></thead><tbody>${filtered.map(attempt => {
    const counted = attempt.included && attempt.selected;
    let status = !attempt.included ? 'Non comptée' : !attempt.selected ? 'Autre tentative' : attempt.completed === true ? 'Terminée' : attempt.completed === false ? 'En cours' : 'Nombre de questions inconnu';
    const started = attempt.started_at && !Number.isNaN(Date.parse(attempt.started_at)) ? dateFormat.format(new Date(attempt.started_at)) : 'Date indisponible';
    return `<tr class="${counted ? '' : 'excluded-row'}"><td>${escapeHTML(started)}</td><td>${escapeHTML(names[attempt.reading_id] || 'Non attribuée')}</td><td><strong>${attempt.answered_questions === null ? '—' : number(attempt.answered_questions)}</strong>${attempt.question_count ? ' / ' + number(attempt.question_count) : ''}</td><td><span class="status-pill ${!counted ? 'secondary' : attempt.completed ? '' : 'partial'}">${status}</span></td><td>${attempt.source_url ? `<a href="${escapeHTML(attempt.source_url)}" target="_blank" rel="noopener noreferrer" aria-label="Ouvrir cette série dans ECNPlus" title="Ouvrir dans ECNPlus">${icon('external')}</a>` : ''}</td></tr>`;
  }).join('')}</tbody></table></div>` : '<p class="detail-empty">Aucune tentative enregistrée pour cette lecture.</p>'}`;
}

$('search').addEventListener('input', renderCourses);
for (const id of ['filter', 'sort']) $(id).addEventListener('change', renderCourses);
$('focus-reading').addEventListener('change', () => {state.focus = $('focus-reading').value; renderCourses();});
$('reset-filters').addEventListener('click', () => {$('search').value=''; $('filter').value='all'; renderCourses(); $('search').focus();});
$('sync-button').addEventListener('click', () => startSync(false));
document.querySelectorAll('[data-settings]').forEach(button => button.addEventListener('click', openSettings));
document.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', () => $(button.dataset.close).close()));
$('course-grid').addEventListener('click', event => {const button = event.target.closest('[data-course]'); if (button) openCourse(button.dataset.course);});
$('detail-content').addEventListener('click', event => {const button = event.target.closest('[data-detail-reading]'); if (button) {state.detailReading=button.dataset.detailReading; renderDetail();}});
for (const dialog of document.querySelectorAll('dialog')) dialog.addEventListener('click', event => {
  if (event.target !== dialog) return;
  const box = dialog.getBoundingClientRect();
  if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) dialog.close();
});
$('settings-form').addEventListener('submit', async event => {
  event.preventDefault(); $('settings-error').hidden = true;
  const form = new FormData(event.target);
  const readings = state.data.settings.readings.map((r, i) => ({id:r.id, name:String(form.get('name-' + r.id)).trim(), starts_on: i ? form.get('date-' + r.id) || null : null}));
  try { await startSettings(readings); }
  catch (error) { $('settings-error').textContent=error.message; $('settings-error').hidden=false; }
});
$('credential-form').addEventListener('submit', async event => {
  event.preventDefault(); $('credential-error').hidden=true; $('save-credential').disabled=true;
  try {
    await api('/api/credential', {authorization:$('credential').value});
    $('credential').value='';
    state.data.credential_configured=true;
    $('connection-state').textContent='Jeton configuré';
    $('credential-note').textContent='Jeton mis à jour. Vous pouvez maintenant synchroniser vos données.';
  } catch (error) { $('credential-error').textContent=error.message; $('credential-error').hidden=false; }
  finally {$('save-credential').disabled=state.data?.job?.status === 'running';}
});
document.addEventListener('keydown', event => {
  if (event.key === '/' && !['INPUT','SELECT','TEXTAREA'].includes(event.target.tagName) && !document.querySelector('dialog[open]')) {event.preventDefault(); $('search').focus();}
});
load().catch(error => {notice(error.message, true, {label:'Réessayer', run:() => load().catch(e => notice(e.message,true))}); $('save-state').textContent='Impossible de charger les données enregistrées'; $('overview-stats').innerHTML=''; $('course-grid').setAttribute('aria-busy','false');});
