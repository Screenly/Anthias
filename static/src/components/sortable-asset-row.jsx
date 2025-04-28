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
    opacity: isDragging ? 0.95 : 1,
    zIndex: isDragging ? 99999 : 'auto',
    position: isDragging ? 'relative' : 'static',
    backgroundColor: isDragging ? 'rgba(255, 255, 255, 0.1)' : 'transparent',
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
