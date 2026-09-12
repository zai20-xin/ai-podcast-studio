<template>
  <div class="studio-shell">
    <aside class="nav-rail">
      <div class="brand" title="AI Podcast Studio">
        <div class="brand-mark">
          <div class="waveform-bar" aria-hidden="true">
            <span v-for="n in 12" :key="n" />
          </div>
        </div>
        <div class="brand-text">
          <strong>Podcast</strong>
          <span>Studio</span>
        </div>
      </div>

      <nav class="nav-list">
        <router-link
          v-for="item in navItems"
          :key="item.path"
          :to="item.path"
          class="nav-item"
          :class="{ active: isActive(item) }"
        >
          <el-icon :size="20"><component :is="item.icon" /></el-icon>
          <span>{{ item.label }}</span>
        </router-link>
      </nav>

      <div class="nav-footer">
        <div class="status-dot" :class="{ on: true }" title="本地服务" />
        <span class="studio-mono">LOCAL</span>
      </div>
    </aside>

    <div class="studio-main">
      <header class="top-strip">
        <div class="strip-left">
          <span class="studio-label">{{ pageTitle }}</span>
        </div>
        <div class="strip-right">
          <div class="waveform-bar ambient" aria-hidden="true">
            <span v-for="n in 12" :key="n" />
          </div>
        </div>
      </header>
      <main class="canvas">
        <router-view />
      </main>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()

const navItems = [
  { path: '/', label: '项目', icon: 'FolderOpened', match: (r) => r.path === '/' || r.path.startsWith('/editor') },
  { path: '/settings', label: '设置', icon: 'Setting', match: (r) => r.path.startsWith('/settings') },
]

const pageTitle = computed(() => {
  if (route.path.startsWith('/settings')) return 'SETTINGS · 系统设置'
  if (route.path.startsWith('/editor')) return 'SESSION · 制作台'
  return 'LIBRARY · 项目库'
})

function isActive(item) {
  return item.match ? item.match(route) : route.path === item.path
}
</script>

<style scoped>
.studio-shell {
  display: flex;
  height: 100vh;
  background: var(--studio-bg);
  overflow: hidden;
}

.nav-rail {
  width: 88px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 20px 12px;
  border-right: 1px solid var(--studio-line);
  background: linear-gradient(180deg, #10151f 0%, #0b0e14 100%);
}

.brand {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  margin-bottom: 28px;
  text-decoration: none;
}

.brand-mark {
  width: 48px;
  height: 48px;
  border-radius: 14px;
  background: var(--studio-amber-dim);
  border: 1px solid rgba(226, 168, 75, 0.35);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 10px 8px;
}

.brand-text {
  display: flex;
  flex-direction: column;
  align-items: center;
  line-height: 1.15;
  font-family: var(--font-display);
}

.brand-text strong {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.12em;
  color: var(--studio-ink);
}

.brand-text span {
  font-size: 10px;
  letter-spacing: 0.18em;
  color: var(--studio-muted);
}

.nav-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
  flex: 1;
}

.nav-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 12px 6px;
  border-radius: 12px;
  color: var(--studio-muted);
  text-decoration: none;
  font-size: 12px;
  transition: background 0.15s ease, color 0.15s ease;
}

.nav-item:hover {
  background: var(--studio-panel-2);
  color: var(--studio-ink);
}

.nav-item.active {
  background: var(--studio-amber-dim);
  color: var(--studio-amber);
}

.nav-footer {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  color: var(--studio-muted);
  font-size: 10px;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--studio-muted);
}

.status-dot.on {
  background: var(--studio-green);
  box-shadow: 0 0 8px rgba(92, 191, 138, 0.7);
}

.studio-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.top-strip {
  height: 48px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 28px;
  border-bottom: 1px solid var(--studio-line);
  background: rgba(18, 23, 34, 0.72);
  backdrop-filter: blur(8px);
}

.strip-right .ambient {
  width: 72px;
  opacity: 0.55;
}

.canvas {
  flex: 1;
  overflow: auto;
  padding: 28px 32px 40px;
}

@media (max-width: 768px) {
  .nav-rail {
    width: 64px;
    padding: 12px 8px;
  }

  .brand-text,
  .nav-item span {
    display: none;
  }

  .canvas {
    padding: 16px;
  }
}
</style>
