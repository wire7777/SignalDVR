package com.signaldvr.app.ui.manager

import android.app.AlertDialog
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.signaldvr.app.R
import com.signaldvr.app.api.ApiClient
import com.signaldvr.app.api.SeriesRecordingRule
import com.signaldvr.app.api.SeriesRecordingUpdateRequest
import kotlinx.coroutines.launch

class DvrManagerFragment : Fragment() {

    private lateinit var recyclerView: RecyclerView
    private lateinit var loading: ProgressBar
    private lateinit var emptyText: TextView
    private lateinit var errorText: TextView
    private lateinit var countText: TextView

    private val adapter = SeriesRuleAdapter(
        onClick = { rule -> showRuleMenu(rule) }
    )

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View {
        return inflater.inflate(
            R.layout.fragment_dvr_manager,
            container,
            false
        )
    }

    override fun onViewCreated(
        view: View,
        savedInstanceState: Bundle?
    ) {
        recyclerView = view.findViewById(R.id.series_rules_recycler)
        loading = view.findViewById(R.id.series_rules_loading)
        emptyText = view.findViewById(R.id.series_rules_empty)
        errorText = view.findViewById(R.id.series_rules_error)
        countText = view.findViewById(R.id.series_rules_count)

        recyclerView.layoutManager = LinearLayoutManager(requireContext())
        recyclerView.adapter = adapter
        recyclerView.itemAnimator = null

        loadSeriesRules(showLoading = true)
    }

    override fun onResume() {
        super.onResume()

        if (::recyclerView.isInitialized) {
            loadSeriesRules(showLoading = false)
        }
    }

    private fun loadSeriesRules(showLoading: Boolean) {
        if (showLoading) {
            loading.visibility = View.VISIBLE
        }

        errorText.visibility = View.GONE

        lifecycleScope.launch {
            try {
                val response = ApiClient
                    .getApi(requireContext())
                    .getSeriesRecordingRules()

                loading.visibility = View.GONE

                if (!response.ok) {
                    showError(
                        response.error ?: "Unable to load series rules"
                    )
                    return@launch
                }

                val rules = response.series
                adapter.submitRules(rules)

                countText.text = when (rules.size) {
                    0 -> "No active series rules"
                    1 -> "1 series rule"
                    else -> "${rules.size} series rules"
                }

                emptyText.visibility =
                    if (rules.isEmpty()) View.VISIBLE else View.GONE

                recyclerView.visibility =
                    if (rules.isEmpty()) View.GONE else View.VISIBLE

                if (rules.isNotEmpty() && !recyclerView.hasFocus()) {
                    recyclerView.post {
                        recyclerView.requestFocus()
                    }
                }

            } catch (e: Exception) {
                loading.visibility = View.GONE
                showError(
                    "Could not load DVR Manager:\n${e.message}"
                )
            }
        }
    }

    private fun showError(message: String) {
        errorText.text = message
        errorText.visibility = View.VISIBLE
        emptyText.visibility = View.GONE
        recyclerView.visibility = View.GONE
        countText.text = "Series rules unavailable"
    }

    private fun showRuleMenu(rule: SeriesRecordingRule) {
        val actions = mutableListOf<String>()

        actions += if (rule.enabled == 1) {
            "Disable Series"
        } else {
            "Enable Series"
        }

        actions += if (rule.onlyNew == 1) {
            "Record All Episodes"
        } else {
            "Record New Episodes Only"
        }

        actions += "Delete Series Rule"

        AlertDialog.Builder(requireContext())
            .setTitle(rule.title ?: "Series Recording")
            .setItems(actions.toTypedArray()) { _, which ->
                when (actions[which]) {
                    "Disable Series" -> updateRule(
                        rule = rule,
                        request = SeriesRecordingUpdateRequest(enabled = 0),
                        successMessage = "Series disabled"
                    )

                    "Enable Series" -> updateRule(
                        rule = rule,
                        request = SeriesRecordingUpdateRequest(enabled = 1),
                        successMessage = "Series enabled"
                    )

                    "Record All Episodes" -> updateRule(
                        rule = rule,
                        request = SeriesRecordingUpdateRequest(onlyNew = 0),
                        successMessage = "Recording all episodes"
                    )

                    "Record New Episodes Only" -> updateRule(
                        rule = rule,
                        request = SeriesRecordingUpdateRequest(onlyNew = 1),
                        successMessage = "Recording new episodes only"
                    )

                    "Delete Series Rule" -> confirmDelete(rule)
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun updateRule(
        rule: SeriesRecordingRule,
        request: SeriesRecordingUpdateRequest,
        successMessage: String
    ) {
        lifecycleScope.launch {
            try {
                val response = ApiClient
                    .getApi(requireContext())
                    .updateSeriesRecordingRule(
                        seriesId = rule.id,
                        request = request
                    )

                if (response.ok) {
                    Toast.makeText(
                        requireContext(),
                        successMessage,
                        Toast.LENGTH_SHORT
                    ).show()

                    loadSeriesRules(showLoading = false)
                } else {
                    Toast.makeText(
                        requireContext(),
                        response.error ?: "Update failed",
                        Toast.LENGTH_LONG
                    ).show()
                }

            } catch (e: Exception) {
                Toast.makeText(
                    requireContext(),
                    "Update failed: ${e.message}",
                    Toast.LENGTH_LONG
                ).show()
            }
        }
    }

    private fun confirmDelete(rule: SeriesRecordingRule) {
        AlertDialog.Builder(requireContext())
            .setTitle("Delete series rule?")
            .setMessage(
                "Stop automatically recording " +
                        "${rule.title ?: "this series"}?"
            )
            .setPositiveButton("Delete") { _, _ ->
                deleteRule(rule)
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun deleteRule(rule: SeriesRecordingRule) {
        lifecycleScope.launch {
            try {
                val response = ApiClient
                    .getApi(requireContext())
                    .deleteSeriesRule(rule.id)

                if (response.ok) {
                    Toast.makeText(
                        requireContext(),
                        response.message ?: "Series rule deleted",
                        Toast.LENGTH_SHORT
                    ).show()

                    loadSeriesRules(showLoading = false)
                } else {
                    Toast.makeText(
                        requireContext(),
                        response.error ?: "Delete failed",
                        Toast.LENGTH_LONG
                    ).show()
                }

            } catch (e: Exception) {
                Toast.makeText(
                    requireContext(),
                    "Delete failed: ${e.message}",
                    Toast.LENGTH_LONG
                ).show()
            }
        }
    }
}

private class SeriesRuleAdapter(
    private val onClick: (SeriesRecordingRule) -> Unit
) : RecyclerView.Adapter<SeriesRuleAdapter.SeriesRuleViewHolder>() {

    private val rules = mutableListOf<SeriesRecordingRule>()

    init {
        setHasStableIds(true)
    }

    fun submitRules(newRules: List<SeriesRecordingRule>) {
        rules.clear()
        rules.addAll(newRules)
        notifyDataSetChanged()
    }

    override fun getItemId(position: Int): Long {
        return rules[position].id.toLong()
    }

    override fun onCreateViewHolder(
        parent: ViewGroup,
        viewType: Int
    ): SeriesRuleViewHolder {
        val view = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_series_rule, parent, false)

        return SeriesRuleViewHolder(view)
    }

    override fun onBindViewHolder(
        holder: SeriesRuleViewHolder,
        position: Int
    ) {
        holder.bind(rules[position])
    }

    override fun getItemCount(): Int = rules.size

    inner class SeriesRuleViewHolder(
        itemView: View
    ) : RecyclerView.ViewHolder(itemView) {

        private val title: TextView =
            itemView.findViewById(R.id.series_rule_title)

        private val details: TextView =
            itemView.findViewById(R.id.series_rule_details)

        private val status: TextView =
            itemView.findViewById(R.id.series_rule_status)

        init {
            itemView.isFocusable = true
            itemView.isFocusableInTouchMode = true

            itemView.setOnClickListener {
                val position = bindingAdapterPosition

                if (position != RecyclerView.NO_POSITION) {
                    onClick(rules[position])
                }
            }

            itemView.setOnFocusChangeListener { view, hasFocus ->
                view.setBackgroundResource(
                    if (hasFocus) {
                        R.drawable.bg_channel_focused
                    } else {
                        R.drawable.bg_channel_normal
                    }
                )
            }
        }

        fun bind(rule: SeriesRecordingRule) {
            title.text = rule.title ?: "Untitled Series"

            val detailsList = mutableListOf<String>()

            if (!rule.channel.isNullOrBlank()) {
                detailsList += "Channel ${rule.channel}"
            }

            detailsList += if (rule.onlyNew == 1) {
                "New episodes only"
            } else {
                "All episodes"
            }

            if (rule.startPadding > 0 || rule.endPadding > 0) {
                detailsList +=
                    "Padding ${rule.startPadding}m / ${rule.endPadding}m"
            }

            if (rule.keepLast > 0) {
                detailsList += "Keep last ${rule.keepLast}"
            }

            details.text = detailsList.joinToString("  •  ")

            status.text = if (rule.enabled == 1) {
                "ENABLED"
            } else {
                "DISABLED"
            }

            status.alpha = if (rule.enabled == 1) 1f else 0.55f
            itemView.alpha = if (rule.enabled == 1) 1f else 0.68f
        }
    }
}