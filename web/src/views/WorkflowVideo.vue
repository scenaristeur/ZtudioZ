<template>
  <div>
    <h1>Vidéos</h1>
    <div class="card toolbar">
      <label>Scène :</label>
      <select v-model="sceneNum" @change="load" class="select">
        <option v-for="s in scenes" :key="s.number" :value="s.number">Scène {{ s.number }}</option>
      </select>
      <button class="btn primary" @click="generate" :disabled="loading" style="margin-left: 1rem;">
        {{ loading ? 'Génération...' : 'Générer les vidéos' }}
      </button>
    </div>

    <div v-if="shots.length" class="shots-list">
      <div v-for="shot in shots" :key="shot.index" class="shot-editor card">
        <div class="shot-editor-left">
          <video v-if="shot.url" :src="shot.url" controls class="shot-preview" />
          <div v-else class="shot-placeholder">Pas de vidéo</div>
          <div class="shot-meta">
            <strong>Plan {{ shot.index }}</strong>
            <span class="tag" v-if="shot.size">{{ formatSize(shot.size) }}</span>
          </div>
        </div>
        <div class="shot-editor-right">
          <div class="field">
            <label>Prompt (vidéo)</label>
            <textarea v-model="shot.prompt_video" rows="3" class="input" />
          </div>
          <div class="field-row">
            <div class="field">
              <label>Modèle</label>
              <select v-model="shot.model" class="select">
                <option value="wan-fast">wan-fast</option>
                <option value="grok-video-pro">grok-video-pro</option>
                <option value="seedance">seedance</option>
              </select>
            </div>
            <div class="field">
              <label>Seed</label>
              <input type="number" v-model.number="shot.seed" class="input" />
            </div>
          </div>
          <div class="shot-actions">
            <button class="btn primary" @click="regenerate(shot)" :disabled="shot._generating">
              {{ shot._generating ? 'En cours...' : 'Régénérer' }}
            </button>
          </div>
          <div v-if="shot._status" class="shot-status" :class="shot._status">
            {{ shot._statusMsg }}
          </div>
        </div>
      </div>
    </div>
    <div v-else class="card empty">Aucune vidéo pour cette scène</div>
  </div>
</template>

<script>
import { useProjectStore } from '../stores/project.js'
export default {
  name: 'WorkflowVideo',
  setup() { return { store: useProjectStore() } },
  data() { return { sceneNum: 1, shots: [], loading: false } },
  computed: { scenes() { return this.store.scenes } },
  methods: {
    formatSize(b) { return b > 1048576 ? (b/1048576).toFixed(1)+' MB' : (b/1024).toFixed(0)+' KB' },
    async load() {
      this.shots = []
      try {
        const res = await fetch(`/api/assets/${this.store.slug}/videos/${this.sceneNum}`)
        if (!res.ok) return
        const data = await res.json()
        this.shots = (data.shots || []).map(s => ({
          ...s,
          prompt_video: '',
          model: 'wan-fast',
          seed: 42,
          _generating: false,
          _status: null,
        }))
      } catch (e) { /* */ }
    },
    async generate() {
      this.loading = true
      await this.store.runStep('produce', this.sceneNum)
      this.loading = false
      setTimeout(() => this.load(), 3000)
    },
    async regenerate(shot) {
      shot._generating = true
      await this.store.runStep('produce', this.sceneNum)
      shot._generating = false
      shot._status = 'success'
      setTimeout(() => this.load(), 3000)
    },
  },
  mounted() { this.load() },
}
</script>
