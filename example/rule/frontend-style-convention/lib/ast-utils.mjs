import fs from 'node:fs'
import { parse as parseSfc } from '@vue/compiler-sfc'
import { parse as parseTemplate, NodeTypes } from '@vue/compiler-dom'

export { NodeTypes }

export function loadVueContext(filePath) {
  const source = fs.readFileSync(filePath, 'utf8')
  const sfc = parseSfc(source, { filename: filePath })
  const template = sfc.descriptor.template?.content || ''
  const ast = template.trim()
    ? parseTemplate(template, { comments: false })
    : null
  return { source, sfc, template, ast }
}

export function getProp(node, names) {
  for (const prop of node.props || []) {
    if (prop.type === NodeTypes.ATTRIBUTE && names.includes(prop.name)) {
      return prop.value?.content || ''
    }
    if (prop.type === NodeTypes.DIRECTIVE && names.includes(prop.arg?.content || '')) {
      if (prop.exp?.content) return prop.exp.content
      return '__dynamic__'
    }
  }
  return ''
}

export function hasDirective(node, name) {
  return (node.props || []).some((prop) =>
    prop.type === NodeTypes.DIRECTIVE && prop.name === name
  )
}

export function textContent(node) {
  if (!node) return ''
  if (node.type === NodeTypes.TEXT) return node.content || ''
  if (node.type === NodeTypes.INTERPOLATION) return '__dynamic__'
  if (!node.children) return ''
  return node.children.map(textContent).join(' ')
}

export function walk(node, visit) {
  visit(node)
  for (const child of node.children || []) walk(child, visit)
  if (node.branches) {
    for (const branch of node.branches) walk(branch, visit)
  }
}

export function staticClassList(node) {
  const cls = getProp(node, ['class']) || ''
  return cls.split(/\s+/).map((s) => s.trim()).filter(Boolean)
}

export function hasRawHex(value) {
  return /#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b/.test(value || '')
}

export function childLooksIconOnly(child) {
  if (child.type !== NodeTypes.ELEMENT) return false
  const tag = (child.tag || '').toLowerCase()
  if (tag === 'svg' || tag === 'icon') return true
  if (tag.includes('icon')) return true
  const cls = getProp(child, ['class']) || ''
  return /\bicon\b/i.test(cls)
}

export function isIconOnlyButton(node) {
  const children = node.children || []
  const meaningfulText = children.map(textContent).join(' ').replace(/\s+/g, ' ').trim()
  if (meaningfulText && meaningfulText !== '__dynamic__') return false
  const elementChildren = children.filter((child) => child.type === NodeTypes.ELEMENT)
  if (!elementChildren.length) return false
  return elementChildren.every(childLooksIconOnly)
}

export function isClickableContainer(node) {
  const tag = (node.tag || '').toLowerCase()
  if (tag === 'button' || tag === 'a') return false
  if (hasDirective(node, 'on')) {
    const role = getProp(node, ['role'])
    const dataClickable = getProp(node, ['data-clickable'])
    return role === 'button' || dataClickable === 'true' || tag === 'div' || tag === 'article' || tag === 'section'
  }
  const role = getProp(node, ['role'])
  const dataClickable = getProp(node, ['data-clickable'])
  return role === 'button' || dataClickable === 'true'
}

export function isInteractiveElement(node) {
  // Case-SENSITIVE: native HTML controls are lowercase (`button`, `a`), while
  // design-system components are PascalCase (`<Button>`). Lowercasing here
  // would wrongly flag `<Button>` — which owns its focus ring via the global
  // `.btn:focus-visible` token — as a bare native control missing focus
  // styling. Mirror the case-sensitive matching in `prefer_ds_primitive`.
  const tag = node.tag || ''
  if (tag === 'button') return true
  if (tag === 'a') {
    return !!getProp(node, ['href']) || hasDirective(node, 'bind')
  }
  const role = getProp(node, ['role'])
  const dataClickable = getProp(node, ['data-clickable'])
  return role === 'button' || dataClickable === 'true'
}
