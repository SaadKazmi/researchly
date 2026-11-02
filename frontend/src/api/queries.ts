import { queryOptions } from "@tanstack/react-query"

import { api, ApiError } from "./client"

export const healthQuery = queryOptions({
  queryKey: ["health"],
  queryFn: async () => {
    const { data, response } = await api.GET("/health")
    if (!data) throw new ApiError(response.status)
    return data
  },
  refetchInterval: 5_000,
})

export const systemQuery = queryOptions({
  queryKey: ["system"],
  queryFn: async () => {
    const { data, response } = await api.GET("/api/v1/system")
    if (!data) throw new ApiError(response.status)
    return data
  },
})
