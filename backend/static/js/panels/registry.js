/* Onboarding a department's estate.
 *
 * Departments hand over the spreadsheets they already keep, and no two name
 * their columns alike. The importer reads whatever headers it is given and maps
 * them itself -- but every decision is shown for confirmation before anything is
 * saved, and each mapping is labelled with how it was made:
 *
 *   exact     the header is our field name
 *   synonym   a spelling we have seen before ("cam_no", "GPS Lat")
 *   fuzzy     matched by similarity, with its score -- confirm this one
 *   unmapped  not imported, and said so rather than guessed
 *
 * The distinction matters most for latitude and longitude. Those two headers
 * score 0.82 against each other, so a careless fuzzy threshold would silently
 * relocate an entire estate.
 */

import { api, session } from '../api.js';
import { el, fill, icon, fmtNum, toast } from '../ui.js';
import { loadCameras } from '../app.js';

let previewData = null;
let pendingFile = null;
const overrides = {};

export function render(host) {
  fill(host,
    el('div.panel-section', {},
      el('div.label', { style: { marginBottom: '10px' }, text: 'Bulk import' }),
      el('div.note', {},
        'Upload a department\'s own camera list in whatever shape it already '
        + 'exists. Nothing is written until you review the mapping below.'),

      el('div', { style: { display: 'flex', gap: '8px', marginTop: '12px', flexWrap: 'wrap' } },
        el('label.btn.primary', { style: { cursor: 'pointer' } },
          icon('upload', 13), 'Choose CSV',
          el('input', {
            type: 'file', accept: '.csv,text/csv,text/plain', hidden: true,
            onchange: (event) => {
              const file = event.target.files?.[0];
              if (file) loadPreview(file, host);
            },
          })),
        el('button.btn', {
          onclick: () => api.download(api.templateUrl(), 'sentinel-camera-template.csv')
            .catch((error) => toast('Download failed', error.message, 'crit')),
        }, icon('download', 13), 'Template'),
      ),
    ),

    el('div#import-result'),
  );

  if (previewData) renderPreview(host);
}

async function loadPreview(file, host) {
  pendingFile = file;
  Object.keys(overrides).forEach((key) => delete overrides[key]);
  const result = document.querySelector('#import-result');
  fill(result, el('div.empty', { text: `Reading ${file.name}…` }));
  try {
    previewData = await api.importPreview(file);
    renderPreview(host);
  } catch (error) {
    previewData = null;
    fill(result, el('div.empty', { style: { color: 'var(--critical)' }, text: error.message }));
  }
}

function renderPreview(host) {
  const result = document.querySelector('#import-result');
  if (!result || !previewData) return;

  if (previewData.error) {
    fill(result, el('div.empty', { text: previewData.error }));
    return;
  }

  const summary = previewData.summary;
  const blocked = summary.missing_required.length > 0;

  fill(result,
    el('div.panel-section', {},
      el('div.metrics', {},
        stat('Columns', `${summary.mapped}/${summary.headers}`, 'mapped'),
        stat('Rows', fmtNum(summary.total_rows), summary.truncated
          ? `showing first ${summary.previewed}` : 'in file'),
        stat('Importable', fmtNum(summary.importable), 'pass validation',
          summary.importable ? 'ok' : 'warn'),
        stat('Rejected', fmtNum(summary.rejected), 'will be skipped',
          summary.rejected ? 'warn' : ''),
      ),

      blocked
        ? el('div.note.warn', { style: { marginTop: '12px' },
            text: `Cannot import: no column maps to ${summary.missing_required.join(', ')}. `
                + 'Pick the right column below, or fix the file.' })
        : summary.fuzzy
          ? el('div.note.warn', { style: { marginTop: '12px' },
              text: `${summary.fuzzy} column${summary.fuzzy === 1 ? ' was' : 's were'} `
                  + 'matched by similarity rather than by a name we recognise. '
                  + 'Check those rows before importing.' })
          : el('div.note.info', { style: { marginTop: '12px' },
              text: 'Every column was matched by an exact name or a spelling we '
                  + 'already know. Nothing here is a guess.' }),
    ),

    /* --- Mapping table ------------------------------------------------- */
    el('div.panel-section', {},
      el('div.label', { style: { marginBottom: '10px' }, text: 'Column mapping' }),
      el('div.scroll-x', {},
        el('table.data', {},
          el('thead', {}, el('tr', {},
            el('th', { text: 'Their column' }),
            el('th', { text: 'Our field' }),
            el('th', { text: 'How' }))),
          el('tbody', {}, ...previewData.mappings.map(mappingRow)),
        )),
    ),

    /* --- Row preview ---------------------------------------------------- */
    el('div.panel-section', {},
      el('div.label', { style: { marginBottom: '10px' }, text: 'Rows as they will be saved' }),
      el('div.scroll-x', { style: { maxHeight: '320px', overflowY: 'auto' } },
        el('table.data', {},
          el('thead', {}, el('tr', {},
            el('th', { text: '#' }),
            el('th', { text: 'Camera ID' }),
            el('th', { text: 'Location' }),
            el('th', { text: 'Position' }),
            el('th', { text: 'Result' }))),
          el('tbody', {}, ...previewData.rows.map(rowPreview)),
        )),
    ),

    el('div.panel-section', { style: { display: 'flex', gap: '8px', alignItems: 'center' } },
      el('button.btn.primary', {
        disabled: blocked || !session.canAct,
        onclick: (event) => commit(event.currentTarget, host),
      }, icon('check', 13), `Import ${fmtNum(summary.importable)} cameras`),
      el('button.btn.ghost', {
        onclick: () => { previewData = null; pendingFile = null; render(host); },
      }, 'Cancel'),
    ),
  );
}

function stat(label, value, sub, kind) {
  return el(`div.metric${kind ? '.' + kind : ''}`, {},
    el('div.m-k', { text: label }),
    el('div.m-v', { text: String(value) }),
    el('div.m-s', { text: sub }));
}

function mappingRow(mapping) {
  const current = overrides[mapping.source] ?? mapping.field ?? '';

  const select = el('select.field', {
    style: { height: '26px', fontSize: '11px' },
    onchange: (event) => {
      const value = event.target.value;
      if (value) overrides[mapping.source] = value;
      else overrides[mapping.source] = '';
    },
  },
    el('option', { value: '', text: '— not imported —', selected: !current }),
    ...previewData.schema.map((field) => el('option', {
      value: field.name,
      text: field.label + (field.required ? ' *' : ''),
      selected: field.name === current,
    })),
  );

  const badge = {
    exact:    el('span.tag.ok',   { text: 'exact' }),
    synonym:  el('span.tag.info', { text: 'known spelling' }),
    fuzzy:    el('span.tag.warn', { text: `similarity ${mapping.score}` }),
    unmapped: el('span.tag',      { text: 'unmapped' }),
  }[mapping.method];

  return el('tr', {},
    el('td', {}, el('span.mono', { text: mapping.source })),
    el('td', {}, select),
    el('td', {}, badge),
  );
}

function rowPreview(row) {
  const values = row.values;
  const hasPosition = values.latitude !== undefined && values.longitude !== undefined;

  return el('tr', {},
    el('td', {}, el('span.faint.mono', { text: String(row.number) })),
    el('td', {}, el('span.mono', { text: values.camera_id || '—' })),
    el('td', { text: values.location_name || values.name || '—' }),
    el('td', {}, hasPosition
      ? el('span.mono.faint', { text: `${values.latitude.toFixed(4)}, ${values.longitude.toFixed(4)}` })
      : el('span.faint', { text: 'no position' })),
    el('td', {}, row.ok
      ? (row.warnings.length
          ? el('span.tag.warn', { text: row.warnings[0].slice(0, 46), title: row.warnings.join('\n') })
          : el('span.tag.ok', { text: 'ready' }))
      : el('span.tag.crit', { text: row.errors[0], title: row.errors.join('\n') })),
  );
}

async function commit(button, host) {
  if (!pendingFile) return;
  button.disabled = true;
  fill(button, 'Importing…');

  // Only send overrides that actually differ from what the importer chose --
  // echoing its own decisions back would make the audit trail claim the
  // operator re-mapped every column by hand.
  const changed = {};
  for (const mapping of previewData.mappings) {
    const override = overrides[mapping.source];
    if (override !== undefined && override !== (mapping.field ?? '')) {
      changed[mapping.source] = override;
    }
  }

  try {
    const result = await api.importCommit(pendingFile, changed);
    toast('Import complete',
      `${result.created} created, ${result.updated} updated, ${result.skipped} skipped. `
      + 'Every field change is in the metadata audit trail.',
      result.skipped ? 'warn' : 'ok', 9000);
    previewData = null;
    pendingFile = null;
    await loadCameras();
    render(host);
  } catch (error) {
    toast('Import failed', error.message, 'crit');
    button.disabled = false;
    fill(button, icon('check', 13), 'Retry import');
  }
}
