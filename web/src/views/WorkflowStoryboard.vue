<template>
  <div>
    <h1>Storyboard</h1>
    <div class="card">
      <label>Scène :</label>
      <select v-model="sceneNum" @change="load" class="select">
        <option v-for="s in scenes" :key="s.number" :value="s.number">Scène {{ s.number }}</option>
      </select>
      <button class="btn primary" @click="generate" :disabled="loading" style="margin-left: 1rem;">
        {{ loading ? 'Génération...' : 'Générer le storyboard' }}
      </button>
    </div>
    <div v-if="board" class="card">
      <h2>Planche board</h2>
      <img v-if="board.board_url" :src="board.board_url" class="board-img" />
    </div>
    <div v-if="board" class="shots-grid">
      <div v-for="shot in board.shots" :key="shot.index" class="shot-card">
        <img v-if="shot.image_url" :src="shot.image_url" class="shot-img" />
        <div class="shot-info">
          <strong>Plan {{ shot.index }}</strong>
          <span class="tag">{{ shot.type }}</span>
          <span class="tag">{{ shot.camera }}</span>
          <p class="shot-desc">{{ shot.description }}</p>
        </div>
      </div>
    </div>
    <div v-if="!board" class="card empty">Sélectionnez une scène</div>
  </div>
</template>

<script>
import { useProjectStore } from '../stores/project.js'
export default {
  name: 'WorkflowStoryboard',
  setup() { return { store: useProjectStore() } },
  data() { return { sceneNum: 1, board: null, loading: false } },
  computed: { scenes() { return this.store.scenes } },
  methods: {
    async load() {
      this.board = null
      try {
        const res = await fetch(`/api/assets/${this.store.slug}/storyboard/${this.sceneNum}`)
        if (res.ok) this.board = await res.json()
      } catch (e) { /* */ }
    },
    async generate() {
      this.loading = true
      await this.store.runStep('storyboard', this.sceneNum)
      this.loading = false
    },
  },
  mounted() { this.load() },
}
</script>
