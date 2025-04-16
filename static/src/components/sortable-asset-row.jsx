import { useSortable } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { AssetRow } from './asset-row'

export const SortableAssetRow = (props) => {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: props.id })

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.85 : 1,
    zIndex: isDragging ? 99999 : 'auto',
    position: isDragging ? 'relative' : 'static',
  }

  return (
    <AssetRow
      {...props}
      ref={setNodeRef}
      style={style}
      dragHandleProps={{ ...attributes, ...listeners }}
      isDragging={isDragging}
    />
  )
}
