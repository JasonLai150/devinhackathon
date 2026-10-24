// DEV FIXTURE component — paginated flight results via the /api/flights stub.
// Relative fetch resolves under /s/<sid>/ thanks to <base href> in base.html.

async function loadFlights(page) {
  const resp = await fetch(`api/flights?page=${page}`);
  const data = await resp.json();
  const el = document.getElementById('results');

  const rows = data.items.map(f => `
    <tr>
      <td>${f.flight_no}</td>
      <td>${f.airline}</td>
      <td>${f.departs_at}</td>
      <td>$${f.price}</td>
      <td>${f.direct ? 'Nonstop' : '1 stop'}</td>
      <td><a class="btn" href="flights/${f.id}">Select</a></td>
    </tr>`).join('');

  el.innerHTML = `
    <table>
      <tr><th>Flight</th><th>Airline</th><th>Departs</th><th>Price</th>
          <th>Type</th><th></th></tr>
      ${rows}
    </table>
    <div class="pager">
      <button id="prev" ${data.page <= 1 ? 'disabled' : ''}>← Prev</button>
      <span class="muted">Page ${data.page} of ${data.total_pages}
            (${data.total} flights)</span>
      <button id="next" ${data.page >= data.total_pages ? 'disabled' : ''}>Next →</button>
    </div>`;

  const prev = document.getElementById('prev');
  const next = document.getElementById('next');
  if (prev) prev.onclick = () => loadFlights(data.page - 1);
  if (next) next.onclick = () => loadFlights(data.page + 1);
}

document.getElementById('search-btn').onclick = () => loadFlights(1);
