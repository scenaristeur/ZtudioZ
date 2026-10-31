<template>
  <div>
    <h1>Audio</h1>
    <div class="card toolbar">
      <label>Scène :</label>
      <select v-model="sceneNum" @change="load" class="select">
        <option v-for="s in scenes" :key="s.number" :value="s.number">Scène {{ s.number }}</option>
      </select>
      <button class="btn primary" @click="generate" :disabled="loading" style="margin-left: 1rem;">
        {{ loading ? 'Génération...' : 'Générer l\'audio' }}
      </button>
    </div>

    <div v-if="files.length" class="audio-list">
      <div v-for="f in files" :key="f.name" class="card audio-card">
        <div class="audio-info">
          <strong>{{ f.name }}</strong>
          <span class="tag">{{ formatSize(f.size) }}</span>
        </div>
        <audio :src="f.url" controls class="audio-player" />
      </div>
    </div>
    <div v-else class="card empty">Aucun audio pour cette scène</div>
  </div>
</template>

<script>
import { useProjectStore } from '../stores/project.js'
export default {
  name: 'WorkflowAudio',
  setup() { return { store: useProjectStore() } },
  data() { return { sceneNum: 1, files: [], loading: false } },
  computed: { scenes() { return this.store.scenes } },
  methods: {
    formatSize(b) { return b > 1048576 ? (b/1048576).toFixed(1)+' MB' : (b/1024).toFixed(0)+' KB' },
    async load() {
      this.files = []
      try {
        const res = await fetch(`/api/assets/${this.store.slug}/audio/${this.sceneNum}`)
        if (!res.ok) return
        const data = await res.json()
        this.files = data.files || []
      } catch (e) { /* */ }
    },
    async generate() {
      this.loading = true
      await this.store.runStep('audio', this.sceneNum)
      this.loading = false
      setTimeout(() => this.load(), 2000)
    },
  },
  mounted() { this.load() },
}
</script>
