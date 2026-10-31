import { defineStore } from 'pinia'

export const useProjectStore = defineStore('project', {
  state: () => ({
    slug: '50eme_rose',
    project: null,
    status: null,
    events: [],
    ws: null,
  }),
  getters: {
    scenes: (s) => s.project?.scenes || [],
    currentStep: (s) => s.status?.checkpoint?.current_step || 'init',
  },
  actions: {
    async init(slug) {
      this.slug = slug || this.slug
      await Promise.all([this.fetchProject(), this.fetchStatus()])
      this.connectWS()
    },
    async fetchProject() {
      const res = await fetch(`/api/projects/${this.slug}`)
      this.project = await res.json()
    },
    async fetchStatus() {
      const res = await fetch(`/api/pipeline/${this.slug}/status`)
      this.status = await res.json()
    },
    async runStep(step, sceneNumber = null, shotNumber = null) {
      const body = {}
      if (sceneNumber) body.scene_number = sceneNumber
      if (shotNumber) body.shot_number = shotNumber
      await fetch(`/api/pipeline/${this.slug}/run/${step}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
    },
    connectWS() {
      if (this.ws) return
      const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
      this.ws = new WebSocket(`${protocol}//${location.host}/ws/${this.slug}`)
      this.ws.onmessage = (e) => {
        const event = JSON.parse(e.data)
        this.events.push(event)
        if (this.events.length > 200) this.events.shift()
        // auto-refresh status on step_complete
        if (event.type === 'step_complete') this.fetchStatus()
      }
      this.ws.onclose = () => { this.ws = null }
    },
    disconnectWS() {
      if (this.ws) { this.ws.close(); this.ws = null }
    },
  },
})
