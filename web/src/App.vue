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

        <section v-if="graph" class="graph">
          <div v-for="(level, index) in graph.levels" :key="index" class="level">
            <div v-for="id in level" :key="id" class="node" :class="nodeClass(id)">
              <div class="node-head">
                <strong>{{ id }}</strong>
                <span class="kind">{{ node(id).is_gate ? 'gate' : 'agent' }}</span>
              </div>
              <p v-if="node(id).agent" class="muted small">{{ node(id).agent }}</p>
              <p v-if="node(id).foreach" class="muted small">
                foreach {{ node(id).foreach }}
              </p>
              <div v-if="node(id).artifact" class="artifact">
                <img
                  v-if="isImage(node(id).artifact)"
                  :src="node(id).artifact.url"
                  :alt="id"
                />
                <video
                  v-else-if="isVideo(node(id).artifact)"
                  :src="node(id).artifact.url"
                  controls
                />
                <a v-else :href="node(id).artifact.url" target="_blank">
                  {{ node(id).artifact.hash }}
                </a>
              </div>
            </div>
          </div>
        </section>

        <section v-if="graph && graph.gate" class="gate">
          <h2>Gate {{ graph.gate.id }}</h2>
          <p>{{ graph.gate.question }}</p>
          <div class="gate-actions">
            <button
              v-for="option in graph.gate.options"
              :key="option"
              :class="option"
              :disabled="busy"
              @click="answer(option)"
            >
              {{ option }}
            </button>
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

const projects = ref([])
const slug = ref('')
const project = ref(null)
const selectedRun = ref('')
const graph = ref(null)
const journal = ref([])
const error = ref('')
const busy = ref(false)
let timer = null

const runs = computed(() => project.value?.runs || [])
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

function isImage(artifact) {
  return (artifact.media_type || '').startsWith('image/')
}

function isVideo(artifact) {
  return (artifact.media_type || '').startsWith('video/')
}

function shortWorkflow(workflow) {
  return (workflow || '').split('#').pop() || 'run'
}

async function load() {
  try {
    const list = await api.projects()
    projects.value = list.projects || []
    if (!slug.value && projects.value.length) {
      slug.value = projects.value[0].slug
    }
    if (!slug.value) {
      project.value = null
      return
    }
    project.value = await api.project(slug.value)
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

function selectProject(next) {
  slug.value = next
  selectedRun.value = ''
  graph.value = null
  journal.value = []
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
    await api.answer(slug.value, selectedRun.value, graph.value.gate.id, option)
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
