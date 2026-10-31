<template>
  <div>
    <h1>Shots / Images</h1>
    <div class="card toolbar">
      <label>Scène :</label>
      <select v-model="sceneNum" @change="load" class="select">
        <option v-for="s in scenes" :key="s.number" :value="s.number">Scène {{ s.number }}</option>
      </select>
      <button class="btn primary" @click="generateAll" :disabled="loading" style="margin-left: 1rem;">
        {{ loading ? 'Génération...' : 'Générer tous les plans' }}
      </button>
    </div>

    <div v-if="shots.length" class="shots-list">
      <div v-for="shot in shots" :key="shot.index" class="shot-editor card">
        <div class="shot-editor-left">
          <img v-if="shot.image_url" :src="shot.image_url" class="shot-preview" />
          <div v-else class="shot-placeholder">Pas d'image</div>
          <div v-if="shot.refs && shot.refs.length" class="shot-refs">
            <label>Références ({{ shot.refs.length }})</label>
            <div class="refs-row">
              <div v-for="ref in shot.refs" :key="ref.slug" class="ref-thumb">
                <img :src="ref.url" :title="ref.slug" />
                <span class="ref-label">{{ ref.slug }}</span>
              </div>
            </div>
          </div>
          <div class="shot-meta">
            <strong>Plan {{ shot.index }}</strong>
            <span class="tag">{{ shot.type }}</span>
            <span class="tag">{{ shot.camera }}</span>
          </div>
        </div>
        <div class="shot-editor-right">
          <div class="field">
            <label>Prompt</label>
            <textarea v-model="shot.prompt_image" rows="3" class="input" />
          </div>
          <div class="field">
            <label>Description</label>
            <textarea v-model="shot.description" rows="2" class="input" />
          </div>
          <div class="field-row">
            <div class="field">
              <label>Modèle</label>
              <select v-model="shot.model" class="select">
                <option value="gptimage">gpt-image-2</option>
                <option value="flux">flux</option>
                <option value="seedream">seedream</option>
              </select>
            </div>
            <div class="field">
              <label>Seed</label>
              <input type="number" v-model.number="shot.seed" class="input" />
            </div>
            <div class="field">
              <label>Durée (s)</label>
              <input type="number" v-model.number="shot.duration_seconds" class="input" />
            </div>
          </div>
          <div class="field">
            <label>Dialogue</label>
            <input v-model="shot.dialogue" class="input" />
          </div>
          <div class="shot-actions">
            <button class="btn primary" @click="regenerate(shot)" :disabled="shot._generating">
              {{ shot._generating ? 'En cours...' : 'Régénérer' }}
            </button>
            <button class="btn" @click="saveShot(shot)">Sauvegarder</button>
          </div>
          <div v-if="shot._status" class="shot-status" :class="shot._status">
            {{ shot._statusMsg }}
          </div>
        </div>
      </div>
    </div>
    <div v-else class="card empty">Aucun plan pour cette scène</div>
  </div>
</template>

<script>
import { useProjectStore } from '../stores/project.js'
export default {
  name: 'WorkflowShots',
  setup() { return { store: useProjectStore() } },
  data() { return { sceneNum: 1, shots: [], loading: false } },
  computed: { scenes() { return this.store.scenes } },
  methods: {
    async load() {
      this.shots = []
      try {
        const res = await fetch(`/api/assets/${this.store.slug}/storyboard/${this.sceneNum}`)
        if (!res.ok) return
        const data = await res.json()
        this.shots = (data.shots || []).map(s => ({
          ...s,
          model: s.model || 'gptimage',
          seed: s.seed || 42,
          _generating: false,
          _status: null,
          _statusMsg: '',
        }))
      } catch (e) { /* */ }
    },
    async generateAll() {
      this.loading = true
      await this.store.runStep('visualize', this.sceneNum)
      this.loading = false
      setTimeout(() => this.load(), 2000)
    },
    async regenerate(shot) {
      shot._generating = true
      shot._status = null
      await this.store.runStep('visualize', this.sceneNum, shot.number)
      shot._generating = false
      shot._status = 'success'
      shot._statusMsg = 'Régénération du plan lancée'
      setTimeout(() => this.load(), 3000)
    },
    async saveShot(shot) {
      try {
        await fetch(`/api/assets/${this.store.slug}/storyboard/${this.sceneNum}/shot/${shot.number}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            prompt_image: shot.prompt_image,
            description: shot.description,
            dialogue: shot.dialogue,
            type: shot.type,
            camera: shot.camera,
            duration_seconds: shot.duration_seconds,
          }),
        })
        shot._status = 'success'
        shot._statusMsg = 'Sauvegardé'
      } catch (e) {
        shot._status = 'error'
        shot._statusMsg = 'Erreur de sauvegarde'
      }
      setTimeout(() => shot._status = null, 2000)
    },
  },
  mounted() { this.load() },
}
</script>
