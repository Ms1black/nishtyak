import type { ProjectFile } from '../model/types'

export interface FileTreeNode {
  name: string
  path: string
  type: 'dir' | 'file'
  file?: ProjectFile
  children: FileTreeNode[]
}

export function buildFileTree(files: ProjectFile[]): FileTreeNode[] {
  const root: FileTreeNode = { name: '', path: '', type: 'dir', children: [] }

  for (const file of files) {
    const parts = file.path.split('/')
    let node = root
    parts.forEach((part, index) => {
      const path = parts.slice(0, index + 1).join('/')
      const isFile = index === parts.length - 1
      let child = node.children.find((c) => c.name === part && c.type === (isFile ? 'file' : 'dir'))
      if (!child) {
        child = { name: part, path, type: isFile ? 'file' : 'dir', file: isFile ? file : undefined, children: [] }
        node.children.push(child)
      }
      node = child
    })
  }

  const sort = (nodes: FileTreeNode[]) => {
    nodes.sort((a, b) => (a.type === b.type ? a.name.localeCompare(b.name) : a.type === 'dir' ? -1 : 1))
    nodes.forEach((n) => sort(n.children))
  }
  sort(root.children)
  return root.children
}
