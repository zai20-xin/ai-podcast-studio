import { defineStore } from 'pinia'
import { ref } from 'vue'
import api from '../api'

export const useProjectStore = defineStore('project', () => {
  const projects = ref([])
  const currentProject = ref(null)
  const episodes = ref([])

  async function fetchProjects() {
    const res = await api.get('/api/projects')
    projects.value = res.data.projects
  }

  async function createProject(name, mode) {
    const res = await api.post('/api/projects', { name, mode })
    projects.value.unshift(res.data)
    return res.data
  }

  async function deleteProject(id) {
    await api.delete(`/api/projects/${id}`)
    projects.value = projects.value.filter(p => p.id !== id)
  }

  async function fetchEpisodes(projectId) {
    const res = await api.get(`/api/podcast/episodes/project/${projectId}`)
    episodes.value = res.data
  }

  return { projects, currentProject, episodes, fetchProjects, createProject, deleteProject, fetchEpisodes }
})
