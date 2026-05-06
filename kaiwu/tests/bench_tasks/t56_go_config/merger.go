package t59_go_config

// deepMerge recursively merges src into dst and returns the result.
// Bug: when both dst and src have a []interface{} value for the same key,
// the src slice REPLACES the dst slice instead of being APPENDED to it.
// The fix is to detect []interface{} on both sides and append.
func deepMerge(dst, src map[string]interface{}) map[string]interface{} {
	out := make(map[string]interface{}, len(dst))
	for k, v := range dst {
		out[k] = v
	}
	for k, srcVal := range src {
		dstVal, exists := out[k]
		if !exists {
			out[k] = srcVal
			continue
		}
		// Recurse into nested maps.
		srcMap, srcIsMap := srcVal.(map[string]interface{})
		dstMap, dstIsMap := dstVal.(map[string]interface{})
		if srcIsMap && dstIsMap {
			out[k] = deepMerge(dstMap, srcMap)
			continue
		}
		// BUG: slice values should be appended, not replaced.
		// The correct behaviour:
		//   srcSlice, srcIsSlice := srcVal.([]interface{})
		//   dstSlice, dstIsSlice := dstVal.([]interface{})
		//   if srcIsSlice && dstIsSlice {
		//       out[k] = append(dstSlice, srcSlice...)
		//       continue
		//   }
		// Instead we fall through and overwrite:
		out[k] = srcVal // BUG: replaces slice instead of appending
	}
	return out
}
