import classNames from 'classnames'
import { SkeletonProps } from '@/types'

export const Skeleton = ({ children, isLoading }: SkeletonProps) => {
  return isLoading ? (
    <span
      className={classNames(
        'placeholder',
        'placeholder-wave',
        'bg-info',
        'rounded-pill',
        'w-100',
      )}
    ></span>
  ) : (
    children
  )
}
