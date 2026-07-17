package com.signaldvr.app.ui.live

import android.content.Intent
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import android.widget.Toast
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.signaldvr.app.R
import com.signaldvr.app.api.ApiClient
import com.signaldvr.app.api.Channel
import com.signaldvr.app.ui.player.PlayerActivity
import kotlinx.coroutines.launch

class LiveTvFragment : Fragment() {

    private lateinit var recycler: RecyclerView
    private lateinit var loading: View
    private lateinit var error: TextView

    override fun onCreateView(inflater: LayoutInflater, container: ViewGroup?, b: Bundle?): View {
        return inflater.inflate(R.layout.fragment_live_tv, container, false)
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        recycler = view.findViewById(R.id.channel_recycler)
        loading  = view.findViewById(R.id.loading)
        error    = view.findViewById(R.id.error_text)

        recycler.layoutManager = LinearLayoutManager(requireContext())

        loadChannels()
    }

    private fun loadChannels() {
        loading.visibility = View.VISIBLE
        error.visibility   = View.GONE

        lifecycleScope.launch {
            try {
                val channels = ApiClient.getApi(requireContext()).getLiveChannels()
                loading.visibility = View.GONE
                recycler.adapter   = ChannelAdapter(channels) { ch ->
                    openPlayer(ch)
                }
                if (channels.isNotEmpty()) {
                    recycler.requestFocus()
                }
            } catch (e: Exception) {
                loading.visibility = View.GONE
                error.visibility   = View.VISIBLE
                error.text         = "Cannot reach server\n${e.message}\n\nCheck Settings → Server URL"
            }
        }
    }

    private fun openPlayer(channel: Channel) {
        val intent = Intent(requireContext(), PlayerActivity::class.java).apply {
            putExtra(PlayerActivity.EXTRA_CHANNEL_NUM,  channel.number)
            putExtra(PlayerActivity.EXTRA_CHANNEL_NAME, channel.name)
            putExtra(PlayerActivity.EXTRA_IS_LIVE,      true)
        }
        startActivity(intent)
    }
}

class ChannelAdapter(
    private val channels: List<Channel>,
    private val onClick: (Channel) -> Unit,
) : RecyclerView.Adapter<ChannelAdapter.VH>() {

    inner class VH(view: View) : RecyclerView.ViewHolder(view) {
        val number:   TextView = view.findViewById(R.id.ch_number)
        val name:     TextView = view.findViewById(R.id.ch_name)
        val nowTitle: TextView = view.findViewById(R.id.ch_now)
        val nextTitle: TextView = view.findViewById(R.id.ch_next)

        init {
            view.isFocusable     = true
            view.isFocusableInTouchMode = true

            view.setOnClickListener {
                onClick(channels[adapterPosition])
            }
            view.setOnFocusChangeListener { v, hasFocus ->
                v.setBackgroundResource(
                    if (hasFocus) R.drawable.bg_channel_focused
                    else R.drawable.bg_channel_normal
                )
            }
        }
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH {
        val v = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_channel, parent, false)
        return VH(v)
    }

    override fun onBindViewHolder(holder: VH, position: Int) {
        val ch = channels[position]
        holder.number.text   = ch.number
        holder.name.text     = ch.name
        holder.nowTitle.text = "NOW: ${ch.nowTitle ?: "No guide data"}"
        holder.nextTitle.text = "NEXT: ${ch.nextTitle ?: ""}"
    }

    override fun getItemCount() = channels.size
}
