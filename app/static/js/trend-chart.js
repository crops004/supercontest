// Shared points-by-week trend chart, used by both the live standings page
// and the history page. Expects on the current page:
//   <canvas id="trend-chart" data-current-user-id="...">
//   <script type="application/json" id="trend-data">{"weeks": [...], "series": [...]}</script>
//   <div id="trend-legend"></div>
//   buttons with [data-trend-mode="all"|"top"] (series filter)
//   buttons with [data-trend-range="full"|"8"|"4"] (week-range filter, optional)
//   rows with [data-user-row="<user_id>"] to hover/tap-highlight a line
(() => {
  const canvas = document.getElementById('trend-chart');
  const dataEl = document.getElementById('trend-data');
  const legendEl = document.getElementById('trend-legend');
  if (!canvas || !dataEl || typeof Chart === 'undefined') return;

  const trend = JSON.parse(dataEl.textContent);
  const currentUserId = parseInt(canvas.dataset.currentUserId || '', 10) || null;

  // Nothing graded yet this season - a 0-vs-0 chart has no meaningful range
  // (Chart.js just pads it to an arbitrary -1..1), so show a placeholder
  // instead of a chart that looks broken.
  const hasAnyPoints = trend.series.some(s => s.cumulative.some(v => v > 0));
  if (!hasAnyPoints) {
    const wrap = canvas.parentElement;
    if (wrap) {
      wrap.innerHTML = `
        <div class="h-full flex flex-col items-center justify-center text-center gap-1 text-copy-lighter">
          <div class="text-2xl">🏈</div>
          <div class="font-medium text-copy-light">Waiting on the season to get going</div>
          <div class="text-xs">This chart fills in once games start wrapping up.</div>
        </div>
      `;
    }
    document.querySelectorAll('[role="group"][aria-label="Week range"], [role="group"][aria-label="Chart view"]')
      .forEach((el) => { el.style.display = 'none'; });
    return;
  }

  const PALETTE = ['#3b82f6', '#f97316', '#10b981', '#f43f5e', '#8b5cf6', '#eab308'];
  const MUTED = 'rgba(148, 163, 184, 0.35)';
  const HIGHLIGHT = '#3b82f6';

  const gridColor = 'rgba(148, 163, 184, 0.15)';
  const textColor = getComputedStyle(document.documentElement)
    .getPropertyValue('--color-copy-lighter') || '#94a3b8';

  const rangeButtons = document.querySelectorAll('[data-trend-range]');

  function weekStartIndex(range) {
    if (range === 'full' || !range) return 0;
    const n = parseInt(range, 10);
    if (!n) return 0;
    return Math.max(0, trend.weeks.length - n);
  }

  function seriesForMode(mode) {
    if (mode === 'all') return trend.series;
    // "top" mode: current user + top 5 by final cumulative points
    const byFinal = [...trend.series].sort((a, b) =>
      (b.cumulative[b.cumulative.length - 1] || 0) - (a.cumulative[a.cumulative.length - 1] || 0)
    );
    const picked = [];
    const seen = new Set();
    const me = trend.series.find(s => s.user_id === currentUserId);
    if (me) { picked.push(me); seen.add(me.user_id); }
    for (const s of byFinal) {
      if (picked.length >= 6) break;
      if (seen.has(s.user_id)) continue;
      picked.push(s);
      seen.add(s.user_id);
    }
    return picked;
  }

  function buildDatasets(mode, range) {
    const startIdx = weekStartIndex(range);
    const picked = seriesForMode(mode);
    return picked.map((s, i) => ({
      userId: s.user_id,
      label: s.display_name,
      data: s.cumulative.slice(startIdx),
      borderColor: mode === 'all'
        ? (s.user_id === currentUserId ? HIGHLIGHT : MUTED)
        : PALETTE[i % PALETTE.length],
      borderWidth: mode === 'all' ? (s.user_id === currentUserId ? 2.5 : 1.5) : 2.5,
      pointRadius: 0,
      tension: 0.15,
    }));
  }

  function labelsForRange(range) {
    const startIdx = weekStartIndex(range);
    return trend.weeks.slice(startIdx).map(w => `Wk ${w}`);
  }

  // Points are cumulative (monotonically non-decreasing), so within any
  // visible window the lowest value for a line is always its first plotted
  // point - no need to scan the whole series.
  function yAxisMin(datasets) {
    const starts = datasets.map(ds => ds.data[0]).filter(v => v !== undefined);
    if (!starts.length) return 0;
    return Math.max(0, Math.floor(Math.min(...starts)) - 2);
  }

  function renderLegend(mode) {
    if (!legendEl) return;
    legendEl.innerHTML = '';
    const entries = mode === 'all'
      ? chart.data.datasets.filter(ds => ds.userId === currentUserId)
      : chart.data.datasets;
    entries.forEach((ds) => {
      const chip = document.createElement('span');
      chip.className = 'inline-flex items-center gap-1.5';
      const isMe = ds.userId === currentUserId;
      chip.innerHTML = `
        <span class="inline-block w-2.5 h-2.5 rounded-full shrink-0" style="background:${ds.borderColor}"></span>
        <span class="text-copy-light">${ds.label}${isMe ? ' (you)' : ''}</span>
      `;
      legendEl.appendChild(chip);
    });
  }

  let currentMode = 'all';
  let currentRange = 'full';
  let pinnedUserId = null;

  const chart = new Chart(canvas.getContext('2d'), {
    type: 'line',
    data: {
      labels: labelsForRange(currentRange),
      datasets: buildDatasets(currentMode, currentRange),
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'nearest', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: (items) => {
              const startIdx = weekStartIndex(currentRange);
              return trend.weeks[startIdx + items[0].dataIndex] !== undefined
                ? `Week ${trend.weeks[startIdx + items[0].dataIndex]}`
                : '';
            },
          },
        },
      },
      scales: {
        x: { grid: { color: gridColor }, ticks: { color: textColor } },
        y: {
          grid: { color: gridColor },
          ticks: { color: textColor },
          suggestedMin: yAxisMin(buildDatasets(currentMode, currentRange)),
        },
      },
    },
  });
  renderLegend('all');

  function redraw() {
    const datasets = buildDatasets(currentMode, currentRange);
    chart.data.labels = labelsForRange(currentRange);
    chart.data.datasets = datasets;
    chart.options.scales.y.suggestedMin = yAxisMin(datasets);
    chart.update();
    renderLegend(currentMode);
  }

  function setActiveButton(group, activeBtn) {
    group.forEach((b) => {
      const active = b === activeBtn;
      b.setAttribute('aria-pressed', active ? 'true' : 'false');
      b.classList.toggle('bg-primary', active);
      b.classList.toggle('text-primary-content', active);
      b.classList.toggle('bg-background', !active);
      b.classList.toggle('text-copy-light', !active);
    });
  }

  document.querySelectorAll('[data-trend-mode]').forEach((btn) => {
    btn.addEventListener('click', () => {
      currentMode = btn.getAttribute('data-trend-mode');
      pinnedUserId = null;
      setActiveButton(document.querySelectorAll('[data-trend-mode]'), btn);
      redraw();
    });
  });

  rangeButtons.forEach((btn) => {
    btn.addEventListener('click', () => {
      currentRange = btn.getAttribute('data-trend-range');
      pinnedUserId = null;
      setActiveButton(rangeButtons, btn);
      redraw();
    });
  });

  function applyHighlight(userId) {
    chart.data.datasets.forEach((ds) => {
      if (ds.userId === userId) {
        ds.borderColor = HIGHLIGHT;
        ds.borderWidth = 3;
      } else {
        ds.borderColor = MUTED;
        ds.borderWidth = 1.5;
      }
    });
    chart.update('none');
  }

  function clearHighlight() {
    chart.data.datasets = buildDatasets(currentMode, currentRange);
    chart.options.scales.y.suggestedMin = yAxisMin(chart.data.datasets);
    chart.update('none');
  }

  document.querySelectorAll('[data-user-row]').forEach((row) => {
    const userId = parseInt(row.getAttribute('data-user-row'), 10);

    row.addEventListener('mouseenter', () => {
      if (pinnedUserId !== null) return;
      applyHighlight(userId);
    });

    row.addEventListener('mouseleave', () => {
      if (pinnedUserId !== null) return;
      clearHighlight();
    });

    row.addEventListener('click', () => {
      if (pinnedUserId === userId) {
        pinnedUserId = null;
        clearHighlight();
      } else {
        pinnedUserId = userId;
        applyHighlight(userId);
      }
    });
  });
})();
