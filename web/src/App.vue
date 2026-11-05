<template>
  <div class="app">
    <aside class="sidebar">
      <div class="brand">ZtudioZ <span>studio v3</span></div>
      <button
        v-for="p in projects"
        :key="p.slug"
        class="project"
        :class="{ active: p.slug === slug }"
        @click="selectProject(p.slug)"
      >
        <span class="project-name">{{ p.name }}</span>
        <span class="badge" :class="p.status">{{ p.status }}</span>
        <span v-if="p.gate" class="gate-hint">gate {{ p.gate }}</span>
      </button>
      <p v-if="!projects.length" class="muted small">
        Aucun projet. Un projet = un dossier avec <code>studio.yaml</code>.
      </p>
    </aside>

    <main class="content">
      <p v-if="error" class="error">{{ error }}</p>
      <p v-if="graph && graph.run && graph.run.error" class="error">
        Echec du run : {{ graph.run.error }}
      </p>

      <template v-if="project">
        <header class="project-head">
          <div>
            <h1>{{ project.name }}</h1>
            <p class="muted small">{{ slug }}</p>
          </div>
          <div class="providers">
            <span v-for="(kind, slot) in project.providers" :key="slot">
              {{ slot }}: <strong>{{ kind }}</strong>
            </span>
          </div>
        </header>

        <nav v-if="runs.length" class="runs">
          <button
            v-for="r in runs"
            :key="r.run_id"
            :class="{ active: r.run_id === selectedRun }"
            @click="selectRun(r.run_id)"
          >
            <span>{{ shortWorkflow(r.workflow) }}</span>
            <span class="badge" :class="r.status">{{ r.status }}</span>
          </button>
        </nav>
        <p v-else class="muted">Aucun run dans ce projet.</p>

        <section v-if="next" class="next-step">
          <h2>Palier suivant : {{ next.label }}</h2>
          <p class="muted small">
            Herite du run precedent :
            {{ next.inherited.length ? next.inherited.join(', ') : 'aucune entree' }}
          </p>
          <div v-for="key in next.missing" :key="key" class="next-input">
            <label :for="`next-${key}`">{{ key }}</label>
            <textarea
              :id="`next-${key}`"
              v-model="nextInputs[key]"
              rows="3"
              :placeholder="key === 'reference'
                ? 'Ancre visuelle : lieu, lumiere, palette, epoque...'
                : 'Valeur attendue par le workflow (texte ou JSON)'"
            ></textarea>
          </div>
          <p v-if="next.missing.includes('plans')" class="muted small">
            M2 demande des fiches de plans (image + mouvement) : collez le JSON des plans.
          </p>
          <button class="launch" :disabled="busy || !canLaunchNext" @click="launchNext">
            Lancer {{ next.label }}
          </button>
        </section>

        <section v-if="graph" class="workflow">
          <div v-if="graph.run" class="run-summary">
            <span class="badge" :class="graph.run.status">{{ graph.run.status }}</span>
            <span>{{ shortWorkflow(graph.run.workflow) }}</span>
            <span class="muted small">run {{ graph.run.run_id }}</span>
            <span class="muted small">{{ graph.run.cost.toFixed(4) }} credits</span>
          </div>

          <div class="legend">
            <span><i class="dot done"></i> termine</span>
            <span><i class="dot waiting"></i> en attente</span>
            <span><i class="dot gate"></i> gate</span>
            <span><i class="dot"></i> a faire</span>
          </div>

          <div v-for="(level, index) in graph.levels" :key="index" class="stage">
            <div class="stage-head">
              <span class="stage-num">{{ index + 1 }}</span>
              <span class="stage-label">Etape {{ index + 1 }}</span>
              <span class="stage-meta">{{ level.length }} noeud{{ level.length > 1 ? 's' : '' }}</span>
            </div>

            <div class="stage-nodes">
              <div v-for="id in level" :key="id" class="node" :class="nodeClass(id)">
                <div class="node-head">
                  <span class="node-title">
                    <i class="dot" :class="nodeStatus(id)"></i>
                    <strong>{{ id }}</strong>
                  </span>
                  <span class="kind">{{ node(id).is_gate ? 'gate' : 'agent' }}</span>
                </div>
                <p v-if="node(id).agent" class="muted small">{{ node(id).agent }}</p>
                <p v-if="node(id).needs && node(id).needs.length" class="muted small">
                  apres : {{ node(id).needs.join(', ') }}
                </p>
                <p v-if="node(id).foreach" class="muted small">
                  foreach {{ node(id).foreach }}<span v-if="node(id).limit"> · max {{ node(id).limit }}</span>
                </p>
                <p v-if="node(id).is_gate && node(id).question" class="question">
                  {{ node(id).question }}
                </p>

                <div v-if="node(id).media && node(id).media.length" class="media-grid">
                  <template v-for="media in node(id).media" :key="media.hash">
                    <img
                      v-if="isImage(media)"
                      :src="media.url"
                      :alt="`${id} ${media.hash}`"
                    />
                    <video v-else-if="isVideo(media)" :src="media.url" controls />
                  </template>
                </div>

                <JsonArtifact
                  v-if="node(id).artifact && isJson(node(id).artifact)"
                  :url="node(id).artifact.url"
                  :label="`voir ${id} (JSON)`"
                />
                <a
                  v-else-if="node(id).artifact && !node(id).media.length"
                  :href="node(id).artifact.url"
                  target="_blank"
                  class="artifact-link"
                >
                  {{ node(id).artifact.hash }}
                </a>
              </div>
            </div>
          </div>
        </section>

        <section v-if="graph && graph.gate" class="gate">
          <h2>Gate {{ graph.gate.id }}</h2>
          <p>{{ graph.gate.question }}</p>
          <VerdictCard
            v-if="verdictArtifact"
            :url="verdictArtifact.url"
            @loaded="onVerdict"
          />
          <div class="gate-actions">
            <button
              v-for="option in graph.gate.options.filter((o) => o !== 'retry')"
              :key="option"
              :class="option"
              :disabled="busy"
              @click="answer(option)"
            >
              {{ optionLabel(option) }}
            </button>
          </div>

          <div v-if="graph.gate.options.includes('retry')" class="gate-feedback">
            <label for="gate-feedback">Corriger et creer une nouvelle version</label>
            <textarea
              id="gate-feedback"
              v-model="feedback"
              rows="4"
              placeholder="Ce qui doit changer : rythme, nombre de plans, fin, ton..."
            ></textarea>
            <button
              class="retry"
              :disabled="busy || !feedback.trim()"
              @click="answer('retry')"
            >
              {{ retryLabel }}
            </button>
            <span class="muted small">
              La correction relance l'etape precedente sans repartir de zero.
            </span>
            <span v-if="graph.gate.id === 'gate_verdict'" class="muted small">
              Etape rejouee : images puis verdict.
            </span>
          </div>
        </section>

        <section v-if="journal.length" class="journal">
          <h2>Journal</h2>
          <ol>
            <li v-for="event in journal.slice(-18)" :key="event.seq">
              <code>{{ event.type }}</code>
              <span v-if="event.node"> — {{ event.node }}</span>
              <span v-if="event.status" class="muted"> ({{ event.status }})</span>
            </li>
          </ol>
        </section>
      </template>
    </main>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from './api'
import JsonArtifact from './JsonArtifact.vue'
import VerdictCard from './VerdictCard.vue'

const projects = ref([])
const slug = ref('')
const project = ref(null)
const selectedRun = ref('')
const graph = ref(null)
const journal = ref([])
const error = ref('')
const busy = ref(false)
const feedback = ref('')
const next = ref(null)
const nextInputs = ref({})
let timer = null

const runs = computed(() => project.value?.runs || [])
const canLaunchNext = computed(() => {
  if (!next.value) return false
  return next.value.missing.every((key) => (nextInputs.value[key] || '').trim())
})
const verdictArtifact = computed(
  () => (graph.value?.nodes || []).find((node) => node.id === 'verdict')?.artifact || null,
)
const retryLabel = computed(() =>
  graph.value?.gate?.id === 'gate_verdict'
    ? 'Recommencer les images avec cette correction'
    : 'Creer une nouvelle version',
)
const nodes = computed(() =>
  Object.fromEntries((graph.value?.nodes || []).map((node) => [node.id, node])),
)

function node(id) {
  return nodes.value[id] || {}
}

function nodeClass(id) {
  const current = node(id)
  return {
    completed: current.completed,
    gate: current.is_gate,
    waiting: graph.value?.gate?.id === id,
  }
}

function nodeStatus(id) {
  if (graph.value?.gate?.id === id) return 'waiting'
  if (node(id).completed) return 'done'
  if (node(id).is_gate) return 'gate'
  return ''
}

function isImage(artifact) {
  return (artifact.media_type || '').startsWith('image/')
}

function isVideo(artifact) {
  return (artifact.media_type || '').startsWith('video/')
}

function isJson(artifact) {
  return (artifact.media_type || '').includes('json')
}

function shortWorkflow(workflow) {
  return (workflow || '').split('#').pop() || 'run'
}

async function load() {
  try {
    const list = await api.projects()
    projects.value = list.projects || []
    if (!slug.value && projects.value.length) {
      const saved = localStorage.getItem('studio.slug')
      slug.value =
        (saved && projects.value.some((project) => project.slug === saved) && saved) ||
        projects.value[0].slug
    }
    if (!slug.value) {
      project.value = null
      return
    }
    project.value = await api.project(slug.value)
    next.value = (await api.next(slug.value)).next
    if (!next.value) {
      nextInputs.value = {}
    }
    const ids = runs.value.map((run) => run.run_id)
    if (!selectedRun.value || !ids.includes(selectedRun.value)) {
      selectedRun.value = ids[0] || ''
    }
    if (selectedRun.value) {
      graph.value = await api.graph(slug.value, selectedRun.value)
      journal.value = (await api.journal(slug.value, selectedRun.value)).events
    } else {
      graph.value = null
      journal.value = []
    }
    error.value = ''
  } catch (exc) {
    error.value = exc.message
  }
}

function selectProject(nextSlug) {
  slug.value = nextSlug
  localStorage.setItem('studio.slug', nextSlug)
  selectedRun.value = ''
  graph.value = null
  journal.value = []
  next.value = null
  nextInputs.value = {}
  load()
}

function selectRun(runId) {
  selectedRun.value = runId
  load()
}

async function answer(option) {
  if (!graph.value?.gate) return
  busy.value = true
  try {
    await api.answer(
      slug.value,
      selectedRun.value,
      graph.value.gate.id,
      option,
      option === 'retry' ? feedback.value : '',
    )
    feedback.value = ''
    await load()
  } catch (exc) {
    error.value = exc.message
  } finally {
    busy.value = false
  }
}

function optionLabel(option) {
  return { continue: 'Continuer', retry: 'Nouvelle version', stop: 'Arreter' }[option] || option
}

function onVerdict(verdict) {
  if (!verdict || graph.value?.gate?.id !== 'gate_verdict' || feedback.value.trim()) return
  const lines = []
  if (verdict.notes) {
    lines.push(Array.isArray(verdict.notes) ? verdict.notes.join(' ') : verdict.notes)
  }
  for (const constraint of verdict.constraints || []) {
    if (!constraint.respected) {
      lines.push(`- ${constraint.text} : ${constraint.evidence || 'a corriger'}`)
    }
  }
  feedback.value = lines.join('\n')
}

async function launchNext() {
  if (!next.value || !canLaunchNext.value) return
  const inputs = {}
  for (const key of next.value.missing) {
    const raw = (nextInputs.value[key] || '').trim()
    try {
      inputs[key] = raw.startsWith('{') || raw.startsWith('[') ? JSON.parse(raw) : raw
    } catch {
      inputs[key] = raw
    }
  }
  busy.value = true
  try {
    await api.startRun(slug.value, next.value.workflow, inputs)
    next.value = null
    nextInputs.value = {}
    await load()
  } catch (exc) {
    error.value = exc.message
  } finally {
    busy.value = false
  }
}

onMounted(() => {
  load()
  timer = setInterval(load, 2000)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>
