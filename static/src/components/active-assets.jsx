import { useSelector, useDispatch } from 'react-redux'
import {
  selectActiveAssets,
  updateAssetOrder,
  fetchAssets,
} from '@/store/assets'
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
} from '@dnd-kit/core'
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable'
import { SortableAssetRow } from '@/components/sortable-asset-row'
import { useState, useEffect } from 'react'

export const ActiveAssetsTable = () => {
  const dispatch = useDispatch()
  const activeAssets = useSelector(selectActiveAssets)
  const [items, setItems] = useState(activeAssets)

  useEffect(() => {
    setItems(activeAssets)
  }, [activeAssets])

  const sensors = useSensors(
    useSensor(PointerSensor),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    }),
  )

  const handleDragEnd = async (event) => {
    const { active, over } = event

    if (!over || active.id === over.id) {
      return
    }

    const oldIndex = items.findIndex(
      (asset) => asset.asset_id.toString() === active.id,
    )
    const newIndex = items.findIndex(
      (asset) => asset.asset_id.toString() === over.id,
    )

    const newItems = arrayMove(items, oldIndex, newIndex)
    setItems(newItems)

    const activeIds = newItems.map((asset) => asset.asset_id)

    try {
      await dispatch(updateAssetOrder(activeIds.join(','))).unwrap()
      dispatch(fetchAssets())
    } catch (error) {
      setItems(activeAssets)
    }
  }

  return (
    <table className="table">
      <thead className="table-borderless">
        <tr>
          <th className="font-weight-normal asset_row_name">Name</th>
          <th className="font-weight-normal" style={{ width: '21%' }}>
            Start
          </th>
          <th className="font-weight-normal" style={{ width: '21%' }}>
            End
          </th>
          <th className="font-weight-normal" style={{ width: '13%' }}>
            Duration
          </th>
          <th className="font-weight-normal" style={{ width: '7%' }}>
            Activity
          </th>
          <th className="font-weight-normal" style={{ width: '13%' }}></th>
        </tr>
      </thead>
      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        onDragEnd={handleDragEnd}
      >
        <tbody id="active-assets">
          <SortableContext
            items={items.map((a) => a.asset_id.toString())}
            strategy={verticalListSortingStrategy}
          >
            {items.map((asset) => (
              <SortableAssetRow
                key={asset.asset_id}
                id={asset.asset_id.toString()}
                name={asset.name}
                startDate={asset.start_date}
                endDate={asset.end_date}
                duration={asset.duration}
                isEnabled={asset.is_enabled}
                assetId={asset.asset_id}
                isProcessing={asset.is_processing}
              />
            ))}
          </SortableContext>
        </tbody>
      </DndContext>
    </table>
  )
}
