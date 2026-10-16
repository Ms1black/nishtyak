<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ProfileCard } from '@/widgets/profile-card'
import { TeamListCard } from '@/widgets/team-list'

const route = useRoute()
const router = useRouter()
const teamList = ref<InstanceType<typeof TeamListCard> | null>(null)

onMounted(async () => {
  if (route.query.createTeam) {
    await nextTick()
    teamList.value?.focusCreate()
    router.replace({ query: {} })
  }
})
</script>

<template>
  <div class="container profile-page">
    <ProfileCard />
    <TeamListCard ref="teamList" />
  </div>
</template>

<style scoped>
.profile-page {
  display: grid;
  grid-template-columns: 416px minmax(0, 1fr);
  gap: 24px;
  align-items: start;
}

@media (max-width: 1024px) {
  .profile-page {
    grid-template-columns: 1fr;
  }
}
</style>
