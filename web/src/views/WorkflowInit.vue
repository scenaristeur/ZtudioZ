<template>
  <div>
    <h1>Initialisation</h1>
    <div class="card">
      <h2>Projet : {{ slug }}</h2>
      <div class="stat-grid">
        <div class="stat"><span class="stat-value">{{ scenes.length }}</span><span class="stat-label">Scènes</span></div>
        <div class="stat"><span class="stat-value">{{ totalShots }}</span><span class="stat-label">Plans</span></div>
        <div class="stat"><span class="stat-value">{{ videoCount }}</span><span class="stat-label">Vidéos</span></div>
        <div class="stat"><span class="stat-value">{{ audioCount }}</span><span class="stat-label">Audios</span></div>
      </div>
    </div>
    <div class="card">
      <h2>Scènes</h2>
      <div class="scene-grid">
        <div v-for="s in scenes" :key="s.number" class="scene-card" :class="{ done: s.has_storyboard }">
          <div class="scene-num">{{ s.number }}</div>
          <div class="scene-info">
            <div class="scene-status">
              <span class="tag" v-if="s.has_storyboard">storyboard</span>
              <span class="tag">{{ s.shot_count }} plans</span>
            </div>
          </div>
        </div>
      </div>
    </div>
    <div class="card">
      <h2>Événements en direct</h2>
      <div v-if="events.length === 0" class="empty">Aucun événement</div>
      <div v-for="(ev, i) in recentEvents" :key="i" class="event-row">
        <span class="tag" :class="ev.type">{{ ev.type }}</span>
        <span>{{ ev.step }} </span>
        <span v-if="ev.scene">scene {{ ev.scene }}</span>
        <span v-if="ev.error" class="error">{{ ev.error }}</span>
      </div>
    </div>
  </div>
</template>

<script>
import { useProjectStore } from '../stores/project.js'
export default {
  name: 'WorkflowInit',
  setup() { return { store: useProjectStore() } },
  computed: {
    slug() { return this.store.slug },
    scenes() { return this.store.scenes },
    totalShots() { return this.scenes.reduce((a, s) => a + (s.shot_count || 0), 0) },
    videoCount() { return this.store.status?.video_count || 0 },
    audioCount() { return this.store.status?.audio_count || 0 },
    events() { return this.store.events },
    recentEvents() { return this.events.slice(-10).reverse() },
  },
}
</script>
