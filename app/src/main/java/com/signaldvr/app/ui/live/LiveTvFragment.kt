package com.signaldvr.app.ui.live

import android.content.Intent
import android.os.Bundle
import android.util.Log
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.TextView
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.bumptech.glide.Glide
import com.signaldvr.app.R
import com.signaldvr.app.api.ApiClient
import com.signaldvr.app.api.Channel
import com.signaldvr.app.ui.player.PlayerActivity
import kotlinx.coroutines.launch

class LiveTvFragment : Fragment() {

    private lateinit var recycler: RecyclerView
    private lateinit var loading: View
    private lateinit var error: TextView

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View {
        return inflater.inflate(
            R.layout.fragment_live_tv,
            container,
            false
        )
    }

    override fun onViewCreated(
        view: View,
        savedInstanceState: Bundle?
    ) {
        recycler = view.findViewById(R.id.channel_recycler)
        loading = view.findViewById(R.id.loading)
        error = view.findViewById(R.id.error_text)

        recycler.layoutManager =
            LinearLayoutManager(requireContext())

        loadChannels()
    }

    private fun loadChannels() {
        loading.visibility = View.VISIBLE
        error.visibility = View.GONE

        lifecycleScope.launch {
            try {
                val channels =
                    ApiClient
                        .getApi(requireContext())
                        .getLiveChannels()

                Log.d(
                    "SignalDVR",
                    "Loaded ${channels.size} live channels"
                )

                channels.take(10).forEach { channel ->
                    Log.d(
                        "SignalDVR",
                        "Channel ${channel.number}: " +
                                "name='${channel.name}', " +
                                "now='${channel.nowTitle}', " +
                                "next='${channel.nextTitle}', " +
                                "logo='${channel.logo}'"
                    )
                }

                loading.visibility = View.GONE

                recycler.adapter =
                    ChannelAdapter(channels) { channel ->
                        openPlayer(channel)
                    }

                if (channels.isNotEmpty()) {
                    recycler.post {
                        recycler.requestFocus()
                    }
                }
            } catch (e: Exception) {
                Log.e(
                    "SignalDVR",
                    "Failed to load live channels",
                    e
                )

                loading.visibility = View.GONE
                error.visibility = View.VISIBLE
                error.text =
                    "Cannot reach server\n${e.message}\n\n" +
                            "Check Settings → Server URL"
            }
        }
    }

    private fun openPlayer(channel: Channel) {
        val intent =
            Intent(
                requireContext(),
                PlayerActivity::class.java
            ).apply {
                putExtra(
                    PlayerActivity.EXTRA_CHANNEL_NUM,
                    channel.number
                )
                putExtra(
                    PlayerActivity.EXTRA_CHANNEL_NAME,
                    channel.name
                )
                putExtra(
                    PlayerActivity.EXTRA_IS_LIVE,
                    true
                )
            }

        startActivity(intent)
    }
}

class ChannelAdapter(
    private val channels: List<Channel>,
    private val onClick: (Channel) -> Unit
) : RecyclerView.Adapter<ChannelAdapter.VH>() {

    inner class VH(view: View) :
        RecyclerView.ViewHolder(view) {

        val number: TextView =
            view.findViewById(R.id.ch_number)

        val logo: ImageView =
            view.findViewById(R.id.ch_logo)

        val name: TextView =
            view.findViewById(R.id.ch_name)

        val nowTitle: TextView =
            view.findViewById(R.id.ch_now)

        val nextTitle: TextView =
            view.findViewById(R.id.ch_next)

        init {
            view.isFocusable = true
            view.isFocusableInTouchMode = true

            view.setOnClickListener {
                val position = bindingAdapterPosition

                if (position != RecyclerView.NO_POSITION) {
                    onClick(channels[position])
                }
            }

            view.setOnFocusChangeListener { focusedView, hasFocus ->
                focusedView.setBackgroundResource(
                    if (hasFocus) {
                        R.drawable.bg_channel_focused
                    } else {
                        R.drawable.bg_channel_normal
                    }
                )
            }
        }
    }

    override fun onCreateViewHolder(
        parent: ViewGroup,
        viewType: Int
    ): VH {
        val view =
            LayoutInflater
                .from(parent.context)
                .inflate(
                    R.layout.item_channel,
                    parent,
                    false
                )

        return VH(view)
    }

    override fun onBindViewHolder(
        holder: VH,
        position: Int
    ) {
        val channel = channels[position]

        holder.number.text = channel.number
        holder.name.text = channel.name

        val currentProgram =
            channel.nowTitle
                ?.trim()
                ?.takeIf { it.isNotEmpty() }
                ?: "No guide data"

        val nextProgram =
            channel.nextTitle
                ?.trim()
                ?.takeIf { it.isNotEmpty() }
                ?: "No guide data"

        holder.nowTitle.text = "NOW: $currentProgram"
        holder.nextTitle.text = "NEXT: $nextProgram"

        Log.d(
            "SignalDVR",
            "Binding row ${channel.number}: " +
                    "now='$currentProgram', next='$nextProgram'"
        )

        Glide.with(holder.itemView)
            .clear(holder.logo)

        val logoUrl =
            channel.logo
                ?.trim()
                ?.takeIf { it.isNotEmpty() }

        if (logoUrl == null) {
            holder.logo.visibility = View.INVISIBLE
            holder.logo.setImageDrawable(null)
        } else {
            holder.logo.visibility = View.VISIBLE

            Glide.with(holder.itemView)
                .load(logoUrl)
                .fitCenter()
                .into(holder.logo)
        }
    }

    override fun onViewRecycled(holder: VH) {
        Glide.with(holder.itemView)
            .clear(holder.logo)

        holder.logo.setImageDrawable(null)

        super.onViewRecycled(holder)
    }

    override fun getItemCount(): Int {
        return channels.size
    }
}